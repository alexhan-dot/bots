"""한글 스케줄/운영 요청 → 구조화된 작업(op) 파싱. Claude API + 용어 사전 주입."""
import os, json, re, httpx
from services import glossary

API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

SYSTEM_BASE = """당신은 리니지 클래식 부스팅(대리육성) 운영팀의 메시지 파서입니다.
영업자 그룹 채팅에 올라온 한글 메시지(대화체, 은어, 고객 SMS 스크린샷 텍스트 포함)를 JSON으로 변환하세요.

## 작업 유형(type) — 하나의 메시지에 여러 작업이면 "ops" 배열로 모두 추출
- NEW_CHARACTER: 신규 캐릭터 등록. character_raw, class, server, block(Client/Farming),
  schedule={MON..SUN: [{"time":"HH:MM-HH:MM"}] | "OFF"}, missing[누락 요일]
- SCHEDULE_LEDGER: "24. 수 9월 23일 : 10:00 ~ 18:00 (8시간)" 식 장부. 각 줄을 entries로:
  {date, time, kind: "base"|"extra", action: "keep"|"delete"} ("-> 삭제"면 delete, "추가"면 extra)
- STOP: 특정 일자 정지. character_raw, dates. 시간 변경 금지.
- PLAYER_SWAP: 플레이어 교체. character_raw, dates, player. 시간 변경 금지.
- EXTEND: 해당 일자·시프트 연장. character_raw, date, shift_time, new_time.
- HUNTING_GROUND: 사냥터 변경. character_raw, ground(정식 표기), floor(있으면), mode: "fixed"|"alternate",
  grounds[](alternate일 때), scope: "today"|"both_shifts"|"shift:HH:MM-HH:MM"|"permanent", urgent(bool)
- RELOGIN: 재접속 요청. character_raw, reason, urgent=true
- QUESTION: 고객 질문 → 매니저 답변 필요. character_raw(있으면), question(원문 요약)
- INFO: 정보성/리드/잡담. 알림 불필요. summary. (예: "내일 문의 준다고함", "미리 나왔어요")

## 캐릭터 식별 규칙
- 고객 연락처명 패턴 "{서버}서버{캐릭명}{고객명}" (예: 아덴서버돌서재영 → server=아덴, character_raw=돌, customer=서재영)
- "아덴서버 돌 케릭 …" 형태면 server=아덴, character_raw=돌
- 캐릭명이 어디에도 없으면 character_raw=null (봇이 재질문)

## 시간 규칙
- HH:MM-HH:MM만 허용, "24:00 ~ 08:00" → "24:00-08:00". 날짜는 YYYY-MM-DD (연도 미기재 시 올해)
- 상대 날짜(오늘/내일/어제)는 message_time 기준으로 계산

## 미확인 용어
- 사전에 없는 게임 은어/사냥터명/줄임말이 있으면 unknown_terms[]에 원문 그대로 기록 (일반 한국어는 제외)

JSON만 출력: {"ops":[...], "unknown_terms":[...], "confidence": 0~1, "needs_confirm_reason": "..."|null}
"""

def _system() -> str:
    return SYSTEM_BASE + "\n" + glossary.prompt_block()

async def _claude(user: str, system: str) -> dict:
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01"},
            json={"model": MODEL, "max_tokens": 2500, "system": system,
                  "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        txt = r.json()["content"][0]["text"]
        return json.loads(txt.replace("```json", "").replace("```", "").strip())

async def parse(text_kr: str, message_time: str = "") -> dict:
    """반환: {"ops":[op,...], "unknown_terms":[...], "confidence":..}"""
    user = f"message_time: {message_time}\n\n{text_kr}" if message_time else text_kr
    res = await _claude(user, _system())
    # 사전에 이미 있는 용어는 unknown에서 제거
    known = glossary.all_terms()
    res["unknown_terms"] = [t for t in res.get("unknown_terms", []) if t not in known]
    return res

async def revise(op: dict, instruction: str) -> dict:
    res = await _claude(
        f"기존 파싱 결과:\n{json.dumps(op, ensure_ascii=False)}\n\n수정 지시:\n{instruction}\n\n"
        f"수정 반영한 op 하나만 {{\"ops\":[op]}} 형식으로 출력.", _system())
    return res["ops"][0]

async def extract_terms(chat_dump: str) -> list[dict]:
    """과거 채팅 로그에서 용어 후보 일괄 추출 (학습용)"""
    sys = ("리니지 클래식 부스팅 채팅 로그에서 게임 은어·사냥터명·줄임말·클래스명 후보를 추출하세요. "
           "일반 한국어는 제외. JSON 배열만: [{term, guess_korean_full, guess_english, category, "
           "example_sentence}]\n\n이미 등록된 용어(제외):\n" + ", ".join(sorted(glossary.all_terms())))
    res = await _claude(chat_dump, sys)
    return res if isinstance(res, list) else res.get("terms", [])

def summarize_kr(op: dict) -> str:
    t = op.get("type"); ch = op.get("character") or op.get("character_raw") or "?"
    if t == "NEW_CHARACTER":
        sched = op.get("schedule", {})
        days = "\n".join(f"  {d}: {v if isinstance(v,str) else ', '.join(s['time'] for s in v)}" for d, v in sched.items())
        return f"작업: 신규 등록\n캐릭터: {ch} ({op.get('class','?')})\n서버: {op.get('server','?')} / 블록: {op.get('block','?')}\n주간 스케줄:\n{days}"
    if t == "SCHEDULE_LEDGER":
        lines = "\n".join(f"  {e['date']} {e['time']} [{e['kind']}] {'❌삭제' if e['action']=='delete' else '유지'}" for e in op.get("entries", []))
        return f"작업: 스케줄 장부 반영\n캐릭터: {ch}\n{lines}"
    if t == "STOP":
        return f"작업: 정지(OFF)\n캐릭터: {ch}\n일자: {', '.join(op.get('dates',[]))}\n※ 시간 구조 변경 없음"
    if t == "PLAYER_SWAP":
        return f"작업: 플레이어 교체\n캐릭터: {ch}\n일자: {', '.join(op.get('dates',[]))}\n플레이어: {op.get('player','?')}\n※ 시간 구조 변경 없음"
    if t == "EXTEND":
        return f"작업: 시간 연장\n캐릭터: {ch}\n일자: {op.get('date')}\n변경: {op.get('shift_time')} → {op.get('new_time')}"
    if t == "HUNTING_GROUND":
        g = " ↔ ".join(op.get("grounds", [])) if op.get("mode") == "alternate" else f"{op.get('ground')} {op.get('floor') or ''}".strip()
        return f"작업: 사냥터 변경\n캐릭터: {ch}\n사냥터: {g} ({'고정' if op.get('mode')=='fixed' else '번갈아'})\n범위: {op.get('scope')}{' 🔴긴급' if op.get('urgent') else ''}"
    if t == "RELOGIN":
        return f"작업: 재접속 요청 🔴\n캐릭터: {ch}\n사유: {op.get('reason','')}"
    if t == "QUESTION":
        return f"작업: 고객 질문 (매니저 답변 필요)\n캐릭터: {ch}\n질문: {op.get('question')}"
    if t == "INFO":
        return f"정보: {op.get('summary')} (알림 없음, 기록만)"
    return json.dumps(op, ensure_ascii=False)

"""한글 스케줄/운영 요청 → 구조화된 작업(op) 파싱. Claude API + 용어 사전 주입."""
import os, json, re, httpx
from services import glossary

API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

SYSTEM_BASE = """당신은 리니지 클래식 부스팅(대리육성) 운영팀의 메시지 파서입니다.
영업자 그룹 채팅에 올라온 한글 메시지(대화체, 은어, 고객 SMS 스크린샷 텍스트 포함)를 JSON으로 변환하세요.

## 작업 유형(type) — 하나의 메시지에 여러 작업이면 "ops" 배열로 모두 추출
- NEW_CHARACTER: 신규 캐릭터 등록. character_raw, class, server, customer(고객명, 있으면),
  block: "Client"(고객 계정, 기본) | "Farming"(농장 계정 — "농장/파밍/farming"이라고 한 경우만),
  schedule={MON..SUN: [{"time":"HH:MM-HH:MM"}] | "OFF"}, missing[누락 요일],
  ground(사냥터 정식 표기, 있으면), start_date/end_date(YYYY-MM-DD, "내일부터 일주일간" 같은 기간이 있으면)
  예) "테스트 기사 내일부터 일주일간 오후 8시부터 오전 4시까지 바람방에서 사냥"
      → class=기사, ground=바람방, start_date=내일, end_date=내일+6일, 7개 요일 모두 "20:00-04:00"
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
- HH:MM-HH:MM (24시간제)만 허용. 시(時)는 00~24, **24를 넘기지 말 것** (28:00 ❌)
- 오후 N시 = N+12 (오후 8시=20:00), 저녁 8시=20:00, 밤 11시=23:00, 밤 12시·자정=24:00,
  새벽/오전 N시 = N (새벽 4시=04:00, 오전 4시=04:00), 아침 8시=08:00, 낮 12시·정오=12:00
- 자정을 넘기는 시프트는 끝 시간이 시작보다 작게: 오후 8시~오전 4시 → "20:00-04:00" (다음날 04시에 끝남)
- 자정에 시작하는 그 날 밤 시프트는 "24:00-08:00" (기존 시트 표기)
- "24:00 ~ 08:00" → "24:00-08:00". 날짜는 YYYY-MM-DD (연도 미기재 시 올해)
- 상대 날짜(오늘/내일/어제/모레, N일부터 M일간)는 message_time 기준으로 계산
- 기존 캐릭터에 기간 추가("알렉스 내일부터 3일간 20:00-04:00 추가")는 SCHEDULE_LEDGER 로 날짜별 entries(kind=extra)

## 리니지 클래식 배경
- 리니지 클래식(엔씨소프트, 초창기 리니지 복각) 대리육성 업무. 캐릭터 = 고객 계정, 플레이어 = 우리 직원(필리핀 트레이너)
- 사냥터·클래스·은어는 아래 용어 사전의 정식 표기로 바꿔 기록 (예: 바람 방 → 바람방, 상탑 7층 → 상아탑 7층)

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
        return extract_json(r.json()["content"][0]["text"])

def extract_json(txt: str):
    """모델 출력에서 JSON 부분만 추출 (코드펜스·앞뒤 설명문 허용)"""
    txt = txt.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        starts = [i for i in (txt.find("{"), txt.find("[")) if i >= 0]
        if not starts:
            raise
        start = min(starts)
        end = txt.rfind("}" if txt[start] == "{" else "]")
        return json.loads(txt[start:end + 1])

TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-~]\s*(\d{1,2}):(\d{2})\s*$")


def norm_time(t):
    """"20:00-28:00" → "20:00-04:00" (24 넘는 시는 다음날로). 시작 24:00(자정 시작) 표기는 유지"""
    if not isinstance(t, str):
        return t
    m = TIME_RE.match(t)
    if not m:
        return t
    h1, m1, h2, m2 = int(m[1]), m[2], int(m[3]), m[4]
    if h1 > 24: h1 -= 24
    if h2 > 24: h2 -= 24
    return f"{h1:02d}:{m1}-{h2:02d}:{m2}"


def _norm_op(op: dict) -> dict:
    for k in ("shift_time", "new_time", "time"):
        if k in op: op[k] = norm_time(op[k])
    for v in (op.get("schedule") or {}).values():
        if isinstance(v, list):
            for x in v:
                if isinstance(x, dict) and "time" in x: x["time"] = norm_time(x["time"])
    for e in op.get("entries") or []:
        if isinstance(e, dict) and "time" in e: e["time"] = norm_time(e["time"])
    return op


def _hk(h: int, mi: int, nxt: bool) -> str:
    h %= 24
    base = "자정" if h == 0 and not mi else ("오전" if h < 12 else "오후") + f" {h % 12 or 12}시" + (f" {mi}분" if mi else "")
    return ("다음날 " if nxt else "") + base


def time_kr(t) -> str:
    """"20:00-04:00" → "20:00-04:00 (오후 8시~다음날 오전 4시)" """
    m = TIME_RE.match(t or "") if isinstance(t, str) else None
    if not m:
        return str(t)
    h1, m1, h2, m2 = int(m[1]), int(m[2]), int(m[3]), int(m[4])
    start_next = h1 == 24                                   # 24:00 시작 = 그날 밤 자정
    end_next = start_next or (h2 * 60 + m2) <= (h1 * 60 + m1)
    start = "밤 12시(자정)" if start_next and not m1 else _hk(h1, m1, False)
    return f"{t} ({start}~{_hk(h2, m2, end_next)})"


async def parse(text_kr: str, message_time: str = "") -> dict:
    """반환: {"ops":[op,...], "unknown_terms":[...], "confidence":..}"""
    user = f"message_time: {message_time}\n\n{text_kr}" if message_time else text_kr
    res = await _claude(user, _system())
    res["ops"] = [_norm_op(o) for o in res.get("ops", []) if isinstance(o, dict)]
    # 사전에 이미 있는 용어는 unknown에서 제거
    known = glossary.all_terms()
    res["unknown_terms"] = [t for t in res.get("unknown_terms", []) if t not in known]
    return res

async def revise(op: dict, instruction: str) -> dict:
    res = await _claude(
        f"기존 파싱 결과:\n{json.dumps(op, ensure_ascii=False)}\n\n수정 지시:\n{instruction}\n\n"
        f"수정 반영한 op 하나만 {{\"ops\":[op]}} 형식으로 출력.", _system())
    return _norm_op(res["ops"][0])

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
        vals = {d: (v if isinstance(v, str) else ", ".join(time_kr(x["time"]) for x in v)) for d, v in sched.items()}
        if vals and len(set(vals.values())) == 1 and len(vals) == 7:
            days = f"  매일 {next(iter(vals.values()))}"
        else:
            days = "\n".join(f"  {d}: {v}" for d, v in vals.items())
        kind = "농장" if str(op.get("block", "")).lower().startswith("farm") else "고객"
        period = (f"\n기간: {op.get('start_date') or '오늘'} ~ {op.get('end_date') or '계속'}"
                  if op.get("start_date") or op.get("end_date") else "")
        ground = f"\n사냥터: {op['ground']}" if op.get("ground") else ""
        server = op.get("server") or "-"
        return (f"작업: 신규 등록 [{kind} 계정]\n캐릭터: {ch} ({op.get('class') or '?'})\n"
                f"서버: {server} / 고객: {op.get('customer') or '-'}{period}{ground}\n스케줄:\n{days}")
    if t == "SCHEDULE_LEDGER":
        lines = "\n".join(f"  {e['date']} {time_kr(e['time'])} [{'추가' if e.get('kind') == 'extra' else '기본'}] {'❌삭제' if e['action']=='delete' else '유지'}" for e in op.get("entries", []))
        return f"작업: 스케줄 장부 반영\n캐릭터: {ch}\n{lines}"
    if t == "STOP":
        return f"작업: 정지(OFF)\n캐릭터: {ch}\n일자: {', '.join(op.get('dates',[]))}\n※ 시간 구조 변경 없음"
    if t == "PLAYER_SWAP":
        return f"작업: 플레이어 교체\n캐릭터: {ch}\n일자: {', '.join(op.get('dates',[]))}\n플레이어: {op.get('player','?')}\n※ 시간 구조 변경 없음"
    if t == "EXTEND":
        return f"작업: 시간 연장\n캐릭터: {ch}\n일자: {op.get('date')}\n변경: {time_kr(op.get('shift_time'))} → {time_kr(op.get('new_time'))}"
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

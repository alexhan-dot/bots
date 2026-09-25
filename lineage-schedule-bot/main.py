"""
Lineage Schedule Bot — Cloud Run entrypoint
Flows:
  A) 영업자 스케줄 요청 (텔레그램, 한글) → 번역/파싱 → 캐릭명 매칭 → 컨펌 → 시트 반영
     → 디스코드 알림 → 매니저 컨펌 → 영업자에게 확정 회신
  B) 카카오톡 전달 메시지 (텍스트/이미지/음성) → 변환 → 한/영 트레이너 메시지 초안
     → 대표 컨펌 → 트레이너 채널 발송
"""
import os, re, json, logging
from fastapi import FastAPI, Request, Response

from services import telegram as tg
from services import parser, matcher, translate, sheets, notify, media, state, glossary

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

app = FastAPI()

ADMIN_CHAT_ID = os.environ["ADMIN_CHAT_ID"]          # 대표(컨펌 담당) 텔레그램 chat_id
SALES_CHAT_IDS = {x.strip() for x in os.environ.get("SALES_CHAT_IDS", "").split(",") if x.strip()}  # 영업자 chat_id 목록
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")  # 설정 시 텔레그램 헤더로 발신 검증


@app.on_event("startup")
def _init():
    try: glossary.ensure_tab()
    except Exception as e: log.warning("glossary tab init skipped: %s", e)

@app.get("/healthz")
def healthz():
    return {"ok": True}


# ─────────────────────────────────────────────
# Telegram webhook — 모든 인바운드의 진입점
# ─────────────────────────────────────────────
@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    if WEBHOOK_SECRET and req.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return Response(status_code=401)
    update = await req.json()
    # 텔레그램은 200을 못 받으면 같은 update를 재전송 → 중복 처리 방지
    if "update_id" in update and not state.mark_seen(f"tg:{update['update_id']}"):
        return Response(status_code=200)
    try:
        await handle_update(update)
    except Exception as e:
        # 예외로 500을 돌려주면 텔레그램이 무한 재시도하므로 기록 후 사용자에게 알리고 200 반환
        log.exception("update 처리 실패: %s", e)
        chat = (update.get("message") or (update.get("callback_query") or {}).get("message") or {}).get("chat")
        if chat:
            try: await tg.send(str(chat["id"]), f"⚠️ 처리 중 오류가 발생했습니다 ({type(e).__name__}). 관리자에게 문의해주세요.")
            except Exception: pass
    return Response(status_code=200)


async def handle_update(update: dict):
    # 1) 버튼(callback) 처리: 승인/수정/후보선택
    if "callback_query" in update:
        await handle_callback(update["callback_query"])
        return

    msg = update.get("message")
    if not msg:
        return

    chat_id = str(msg["chat"]["id"])

    # 2) 미디어(카카오톡 전달분: 이미지/음성) → Flow B
    if "photo" in msg or "voice" in msg or "audio" in msg or "document" in msg:
        text = await media.extract_text(msg)          # STT / Vision
        await start_kakao_flow(chat_id, text, source_kind=media.kind_of(msg))
        return

    text = (msg.get("text") or "").strip()
    if not text:
        return

    # 2.5) 관리 명령
    if text.startswith("/learn"):
        await handle_learn(chat_id, text); return
    if text.startswith("/glossary"):
        rows = glossary.load(force=True)
        await tg.send(chat_id, f"용어 {len(rows)}개 등록됨. 미확인: " +
                      ", ".join(r["Term"] for r in rows if r.get("Verified") != "Y")); return

    # 3) 수정 지시 답장 (pending 상태에서 텍스트 수신)
    pending = state.get_pending_by_editor(chat_id)
    if pending:
        await apply_edit(pending, text)
        return

    # 4) 영업자 스케줄 요청 → Flow A / 그 외 텍스트(카톡 복붙) → Flow B
    if chat_id in SALES_CHAT_IDS:
        await start_schedule_flow(chat_id, text)
    else:
        await start_kakao_flow(chat_id, text, source_kind="text")
    return


# ─────────────────────────────────────────────
# Flow A: 스케줄 요청 처리
# ─────────────────────────────────────────────
async def start_schedule_flow(sales_chat_id: str, text_kr: str):
    import datetime
    res = await parser.parse(text_kr, message_time=datetime.datetime.now().isoformat(timespec="minutes"))

    # 미확인 용어 → 학습 요청 (처리는 계속 진행)
    if res.get("unknown_terms"):
        await ask_term_meaning(sales_chat_id, res["unknown_terms"])

    ops = res.get("ops", [])
    if not ops:
        await tg.send(sales_chat_id, "처리할 작업을 찾지 못했습니다. 내용을 조금 더 구체적으로 보내주세요.")
        return

    for op in ops:
        await route_op(sales_chat_id, op)


async def route_op(sales_chat_id: str, op: dict, en: str | None = None):
    """op 하나를 캐릭명 매칭 후 영업자 컨펌 단계로 보냄 (애매하면 후보 버튼)"""
    en = en or await translate.kr_to_en(parser.summarize_kr(op))
    if op["type"] == "INFO":                      # 기록만, 알림 없음
        sheets.apply(op); return

    match = matcher.match(op.get("character") or op.get("character_raw") or "")
    if match.kind == "exact":
        op["character"] = match.canonical
    elif op["type"] == "NEW_CHARACTER" and op.get("character_raw"):
        # 신규 등록은 마스터에 없는 게 정상 → 입력한 이름 그대로 사용
        op["character"] = op["character_raw"]
    elif match.kind == "ambiguous" or (match.kind == "none" and op.get("character_raw")):
        sid = state.create(flow="schedule", sales_chat_id=sales_chat_id, op=op, en=en, status="await_char")
        cands = match.candidates if match.kind == "ambiguous" else []
        await tg.send_buttons(sales_chat_id,
            f"캐릭터명 확인: '{op.get('character_raw')}'\n({parser.summarize_kr(op).splitlines()[0]})",
            [(c, f"char|{sid}|{c}") for c in cands] +
            [("➕ 신규 캐릭터", f"char|{sid}|__new__"), ("✏️ 직접 입력", f"edit|{sid}")])
        return
    else:
        # 캐릭명 자체가 없음 → 활성 캐릭터 후보 버튼
        sid = state.create(flow="schedule", sales_chat_id=sales_chat_id, op=op, en=en, status="await_char")
        active = [m["CanonicalName"] for m in sheets.load_master() if m.get("Status") == "Active"][:8]
        await tg.send_buttons(sales_chat_id,
            f"어느 캐릭터 건인가요?\n{parser.summarize_kr(op).splitlines()[0]}",
            [(c, f"char|{sid}|{c}") for c in active] + [("✏️ 직접 입력", f"edit|{sid}")])
        return
    await request_sales_confirm(sales_chat_id, op, en)


async def ask_term_meaning(chat_id: str, terms: list[str]):
    for t in terms[:3]:
        sid = state.create(flow="learn", term=t, status="await_meaning")
        state.set_editor(sid, chat_id)
        await tg.send(chat_id, f"📚 처음 보는 용어예요: \"{t}\"\n"
                               f"뜻을 답장해주세요 (예: 버림받은 땅 / Forsaken Land / 사냥터)\n"
                               f"모르면 '건너뛰기'")


async def handle_learn(chat_id: str, text: str):
    """/learn 용어 = 한글풀이 / 영문 / 분류      또는   /learn_bulk 뒤에 채팅 로그 붙여넣기"""
    if text.startswith("/learn_bulk"):
        dump = text[len("/learn_bulk"):].strip()
        cands = await parser.extract_terms(dump)
        if not cands:
            await tg.send(chat_id, "새 용어 후보를 찾지 못했습니다."); return
        sid = state.create(flow="learn_bulk", cands=cands, status="await_review")
        lines = "\n".join(f"{i+1}. {c['term']} = {c.get('guess_korean_full','?')} / {c.get('guess_english','?')} [{c.get('category','')}]"
                           for i, c in enumerate(cands))
        await tg.send_buttons(chat_id, f"용어 후보 {len(cands)}개:\n{lines}\n\n번호를 답장하면 제외됩니다 (예: 2,5)",
                              [("✅ 전부 등록", f"ok|{sid}"), ("✏️ 일부 제외", f"edit|{sid}")])
        return
    body = text[len("/learn"):].strip()
    if "=" not in body:
        await tg.send(chat_id, "형식: /learn 용어 = 한글풀이 / 영문 / 분류"); return
    term, rest = [x.strip() for x in body.split("=", 1)]
    parts = [x.strip() for x in rest.split("/")]
    glossary.add(term, parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "unknown")
    await tg.send(chat_id, f"등록: {term} = {parts[0]}")


async def request_sales_confirm(sales_chat_id, op, en):
    sid = state.create(flow="schedule", sales_chat_id=sales_chat_id,
                       op=op, en=en, status="await_sales_confirm")
    summary = parser.summarize_kr(op)
    await tg.send_buttons(
        sales_chat_id,
        f"📋 요청 내용 확인\n{summary}\n\n[영문 번역]\n{en}",
        [("✅ 승인", f"ok|{sid}"), ("✏️ 수정", f"edit|{sid}")],
    )


async def handle_callback(cb):
    data = cb["data"]
    chat_id = str(cb["message"]["chat"]["id"])
    action, sid, *rest = data.split("|")
    s = state.get(sid)
    if not s:
        await tg.answer_callback(cb["id"], "만료된 요청입니다.")
        return

    if action == "char":                       # 캐릭 후보 선택
        if rest[0] == "__new__":
            await tg.send(chat_id, "신규 캐릭터는 아래 양식으로 보내주세요:\n"
                          "캐릭명: / 클래스: / 서버: / MON: 08:00-16:00 / TUE: ... / SUN: OFF")
            state.delete(sid); return
        s["op"]["character"] = rest[0]
        await request_sales_confirm(s["sales_chat_id"], s["op"], s["en"])
        state.delete(sid)

    elif action == "ok" and s["flow"] == "learn_bulk":
        for c in s["cands"]:
            glossary.add(c["term"], c.get("guess_korean_full", ""), c.get("guess_english", ""),
                         c.get("category", "unknown"), verified="N")
        await tg.send(chat_id, f"{len(s['cands'])}개 등록 (미확인 표시)")
        state.delete(sid)

    elif action == "edit":                     # 수정 모드 진입
        state.set_editor(sid, chat_id)
        await tg.send(chat_id, "수정할 내용을 답장으로 보내주세요.")

    elif action == "ok" and s["flow"] == "schedule":
        if s.get("status") != "await_sales_confirm":   # 중복 클릭·지난 버튼 → 시트 이중 반영 방지
            await tg.answer_callback(cb["id"], "이미 처리된 요청입니다."); return
        state.update(sid, status="applying")
        op = s["op"]
        try:
            result = sheets.apply(op)
        except Exception:
            state.update(sid, status="await_sales_confirm")   # 실패 시 다시 ✅ 누를 수 있게
            raise
        if op["type"] == "QUESTION":
            state.update(sid, status="await_answer", sheet_result=result)
            msg = f"❓ **Customer question** — {op.get('character','')}\n{s['en']}\nReply with the answer: {notify.BOT_BASE_URL}/answer?sid={sid}"
            await notify.discord(msg)
            await tg.send(chat_id, "매니저에게 질문 전달. 답변 오면 알려드립니다.")
        elif op["type"] == "RELOGIN":
            state.update(sid, status="await_manager", sheet_result=result)
            msg = f"🔴 **URGENT — re-login needed**\n{s['en']}"
            await notify.discord(msg, buttons_sid=sid)
            await tg.send(chat_id, "🔴 긴급 알림 발송 완료.")
        else:
            state.update(sid, status="await_manager", sheet_result=result)
            text_en = notify.format_manager_msg(op, s["en"], result)
            await notify.discord(text_en, buttons_sid=sid)
            await tg.send(chat_id, "✅ 시트 반영 완료. 매니저 컨펌 대기 중입니다.")

    elif action == "mgr_ok":                   # 매니저 컨펌 (디스코드→relay or 텔레그램)
        state.update(sid, status="done")
        await tg.send(s["sales_chat_id"],
                      f"✅ 확정 완료\n{parser.summarize_kr(s['op'])}\n매니저 컨펌이 완료되었습니다.")

    elif action == "ok" and s["flow"] == "kakao":
        if s.get("status") != "await_confirm":
            await tg.answer_callback(cb["id"], "이미 처리된 요청입니다."); return
        state.update(sid, status="sending")
        await notify.send_to_trainers(s["draft_en"], s["draft_kr"])
        if s.get("kakao_user_key"):
            await kakao.send_text(s["kakao_user_key"], f"확인 완료 — 담당 트레이너에게 전달했습니다.\n\n{s['draft_kr']}")
        await tg.send(chat_id, "✅ 트레이너 발송 + 카톡 회신 완료.")
        state.update(sid, status="done")

    await tg.answer_callback(cb["id"], "처리되었습니다.")


async def apply_edit(pending, edit_text):
    """수정 지시를 반영해 재컨펌"""
    sid, s = pending
    if s["flow"] == "learn":
        if edit_text.strip() not in ("건너뛰기", "skip", "pass"):
            parts = [x.strip() for x in edit_text.split("/")]
            glossary.add(s["term"], parts[0], parts[1] if len(parts) > 1 else "",
                         parts[2] if len(parts) > 2 else "unknown")
        state.delete(sid); return
    if s["flow"] == "learn_bulk":
        skip = {int(x) for x in re.findall(r"\d+", edit_text)}
        for i, c in enumerate(s["cands"], 1):
            if i not in skip:
                glossary.add(c["term"], c.get("guess_korean_full", ""), c.get("guess_english", ""),
                             c.get("category", "unknown"), verified="N")
        await tg.send(s.get("editor_chat_id") or ADMIN_CHAT_ID, f"{len(s['cands'])-len(skip)}개 등록 (미확인 표시). /glossary 로 확인")
        state.delete(sid); return
    if s["flow"] == "schedule":
        op = await parser.revise(s["op"], edit_text)
        if op.get("character_raw") != s["op"].get("character_raw"):
            op.pop("character", None)          # 캐릭명이 바뀌었으면 다시 매칭
        state.delete(sid)                      # 이전 버튼 무효화 (새 sid로 재컨펌)
        await route_op(s["sales_chat_id"], op)
        return
    else:
        kr, en = await translate.revise_draft(s["draft_kr"], s["draft_en"], edit_text)
        state.update(sid, draft_kr=kr, draft_en=en)
        await send_kakao_confirm(sid, s["owner_chat_id"], kr, en)
    state.clear_editor(sid)


# ─────────────────────────────────────────────
# Flow B: 카카오톡 전달 메시지 → 트레이너 안내
# ─────────────────────────────────────────────
async def start_kakao_flow(chat_id: str, source_text: str, source_kind: str):
    kr, en = await translate.draft_trainer_message(source_text)
    sid = state.create(flow="kakao", owner_chat_id=chat_id,
                       source=source_text, source_kind=source_kind,
                       draft_kr=kr, draft_en=en, status="await_confirm")
    await send_kakao_confirm(sid, chat_id, kr, en)


async def send_kakao_confirm(sid, chat_id, kr, en, header="📨 트레이너 전달 초안\n"):
    await tg.send_buttons(
        chat_id,
        f"{header}\n[한글]\n{kr}\n\n[English]\n{en}",
        [("✅ 승인·발송", f"ok|{sid}"), ("✏️ 수정", f"edit|{sid}")],
    )


# ─────────────────────────────────────────────
# 카카오 상담톡 웹훅 (TalkBridge 개발자 모드)
#  신호만 옴 → 서명검증 → 멱등 → 즉시 200 → 백그라운드에서 본문 조회·처리
# ─────────────────────────────────────────────
from fastapi import BackgroundTasks
from services import kakao

@app.get("/confirm")
async def manager_confirm(sid: str):
    s = state.get(sid)
    if not s: return {"ok": False}
    state.update(sid, status="done")
    await tg.send(s["sales_chat_id"], f"✅ 확정 완료\n{parser.summarize_kr(s['op'])}\n매니저 컨펌이 완료되었습니다.")
    return {"ok": True, "message": "Confirmed"}

@app.get("/answer")
async def manager_answer(sid: str, text: str = ""):
    """매니저가 디스코드 알림의 링크에 ?text=답변 으로 응답"""
    s = state.get(sid)
    if not s: return {"ok": False}
    kr = await translate.en_to_kr(text) if text else ""
    state.update(sid, status="done", answer=text)
    await tg.send(s["sales_chat_id"], f"💬 매니저 답변\n질문: {s['op'].get('question')}\n답변: {kr}\n(EN: {text})")
    return {"ok": True}

@app.post("/kakao/webhook")
async def kakao_webhook(req: Request, bg: BackgroundTasks):
    raw = await req.body()
    if not kakao.verify_signature(raw, req.headers.get("X-Bridge-Timestamp", ""),
                                  req.headers.get("X-Bridge-Signature", "")):
        return Response(status_code=401, content="bad signature")
    evt = json.loads(raw)
    if evt.get("test"):                           # 연결 테스트 신호
        return {"ok": True}
    if evt.get("kind") != "message":              # agent echo / reference / ended 등 무시
        return {"ok": True}
    if not state.mark_seen(f"kakao:{evt['brand']}:{evt.get('seq')}"):   # 멱등
        return {"ok": True}
    bg.add_task(process_kakao_message, evt["userKey"])
    return {"ok": True}

async def process_kakao_message(user_key: str):
    m = await kakao.fetch_latest_message(user_key)
    if not m:
        return
    text, kind = await media.from_kakao_text(m.get("text", ""))
    kr, en = await translate.draft_trainer_message(text)
    sid = state.create(flow="kakao", owner_chat_id=ADMIN_CHAT_ID, kakao_user_key=user_key,
                       source=text, source_kind=kind, draft_kr=kr, draft_en=en,
                       status="await_confirm")
    # 영업자에게 접수 안내 (카톡)
    await kakao.send_text(user_key, "접수되었습니다. 확인 후 다시 안내드리겠습니다.")
    await send_kakao_confirm(sid, ADMIN_CHAT_ID, kr, en,
                             header=f"📨 카카오톡 수신 ({kind})\n[원문]\n{text}\n")

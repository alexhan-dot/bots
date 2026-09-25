"""디스코드로 보내는 알림.

봇 토큰 + 채널 ID 가 있으면 봇이 직접 **버튼 달린 카드**를 올림 (매니저가 버튼으로 확인·답장 → 텔레그램 영업자에게 회신).
없으면 예전처럼 웹훅에 텍스트만 (링크 컨펌).
"""
import os, json, logging, httpx

log = logging.getLogger("notify")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
BOT_BASE_URL = os.environ.get("BOT_BASE_URL", "")
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
REQUESTS_CH = os.environ.get("DISCORD_REQUESTS_CHANNEL_ID", "")    # 영업 요청 카드
LOG_CH = os.environ.get("DISCORD_LOG_CHANNEL_ID", "")              # 봇 기록 (OT·플랜 저장 등)
URGENT_ROLE = os.environ.get("DISCORD_URGENT_ROLE_ID", "")         # 긴급(재접속)·컨펌 알림 때 멘션할 역할
CONFIRM_CH = os.environ.get("DISCORD_CONFIRM_CHANNEL_ID", "")      # #schedule-confirm — 일별·주별 컨펌 카드
REPORTS_CH = os.environ.get("DISCORD_REPORTS_CHANNEL_ID", "")      # #shift-reports — 플레이어 스크린샷 기록
API = "https://discord.com/api/v10"
GREEN, RED, GREY, BLUE = 3, 4, 2, 1

KIND = {  # op type → (제목 아이콘, 색)
    "QUESTION": ("❓ Customer question", 0x3498DB),
    "RELOGIN": ("🔴 URGENT — re-login needed", 0xE74C3C),
    "NEW_CHARACTER": ("🆕 New character", 0x9B59B6),
    "STOP": ("⛔ Stop", 0xE67E22),
}


def cards_enabled() -> bool:
    return bool(BOT_TOKEN and REQUESTS_CH)


async def _bot(method: str, path: str, payload: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.request(method, f"{API}{path}", json=payload,
                            headers={"Authorization": f"Bot {BOT_TOKEN}"})
    if r.status_code >= 300:
        log.warning("discord %s %s → %s %s", method, path, r.status_code, r.text[:300])
        return {}
    return r.json() if r.content else {}


async def post(channel_id: str, payload: dict) -> dict:
    return await _bot("POST", f"/channels/{channel_id}/messages", payload)


async def post_file(channel_id: str, payload: dict, filename: str, data: bytes, content_type: str) -> dict:
    """이미지를 첨부해 채널에 올림 (디스코드에 영구 보관 — 슬래시 명령 첨부 URL은 만료됨)"""
    payload = {**payload, "attachments": [{"id": 0, "filename": filename}]}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/channels/{channel_id}/messages", headers={"Authorization": f"Bot {BOT_TOKEN}"},
                         data={"payload_json": json.dumps(payload)},
                         files={"files[0]": (filename, data, content_type)})
    if r.status_code >= 300:
        log.warning("discord file post → %s %s", r.status_code, r.text[:300])
        return {}
    return r.json()


async def log_line(text: str) -> bool:
    """기록 채널에 한 줄. 채널이 없으면 False (호출한 쪽이 다른 방법으로 남김)"""
    if not (BOT_TOKEN and LOG_CH):
        return False
    await post(LOG_CH, {"content": text[:1900], "allowed_mentions": {"parse": []}})
    return True


# ── 영업 요청 카드 ────────────────────────────
def request_card(sid: str, op: dict, en: str, sheet_result: str = "", sales_name: str = "",
                 status: str = "", open_: bool = True) -> dict:
    """영업자 요청 → 매니저용 카드 (embed + 버튼). status 는 처리 후 표시할 한 줄"""
    t = op.get("type", "")
    title, color = KIND.get(t, ("📅 Schedule change", 0x2ECC71))
    ch = op.get("character") or op.get("character_raw") or ""
    fields = [{"name": "Request", "value": (en or "—")[:1024], "inline": False}]
    if ch: fields.insert(0, {"name": "Character", "value": ch, "inline": True})
    if sales_name: fields.insert(1 if ch else 0, {"name": "From (sales)", "value": sales_name, "inline": True})
    if sheet_result and t != "QUESTION":
        fields.append({"name": "Sheet", "value": sheet_result[:1024], "inline": False})
    if status:
        fields.append({"name": "Status", "value": status[:1024], "inline": False})
    embed = {"title": f"{title}" + (f" — {ch}" if ch else ""), "color": color if open_ else 0x95A5A6,
             "fields": fields}
    buttons = []
    if open_:
        if t == "QUESTION":
            buttons = [{"type": 2, "style": BLUE, "label": "Answer", "emoji": {"name": "💬"}, "custom_id": f"req_reply|{sid}"}]
        else:
            buttons = [{"type": 2, "style": GREEN, "label": "Done" if t == "RELOGIN" else "Confirm",
                        "emoji": {"name": "✅"}, "custom_id": f"req_ok|{sid}"},
                       {"type": 2, "style": GREY, "label": "Reply", "emoji": {"name": "💬"}, "custom_id": f"req_reply|{sid}"}]
        if ch and t not in ("QUESTION",):
            buttons.append({"type": 2, "style": GREY, "label": "Schedule", "emoji": {"name": "📋"},
                            "custom_id": f"req_sched|{sid}"})
    return {"embeds": [embed], "components": [{"type": 1, "components": buttons}] if buttons else [],
            "allowed_mentions": {"parse": [], "roles": [URGENT_ROLE] if URGENT_ROLE else []}}


async def send_request(sid: str, op: dict, en: str, sheet_result: str = "", sales_name: str = ""):
    """영업 요청을 디스코드로. 카드가 가능하면 카드, 아니면 웹훅 텍스트"""
    if cards_enabled():
        payload = request_card(sid, op, en, sheet_result, sales_name)
        if op.get("type") == "RELOGIN" and URGENT_ROLE:
            payload["content"] = f"<@&{URGENT_ROLE}>"
        await post(REQUESTS_CH, payload)
        return
    t = op.get("type")                                   # 웹훅 폴백 (링크 방식)
    if t == "QUESTION":
        text = f"❓ **Customer question** — {op.get('character','')}\n{en}\nReply: {BOT_BASE_URL}/answer?sid={sid}&text="
    elif t == "RELOGIN":
        text = f"🔴 **URGENT — re-login needed**\n{en}\n✅ Done: {BOT_BASE_URL}/confirm?sid={sid}"
    else:
        text = f"📅 **Schedule Update**\n{en}\n\nSheet: {sheet_result}\n✅ Confirm: {BOT_BASE_URL}/confirm?sid={sid}"
    await discord(text)


async def discord(text: str, buttons_sid: str | None = None):
    if not DISCORD_WEBHOOK:
        return
    if buttons_sid:
        text = text.replace("{sid}", buttons_sid)
    async with httpx.AsyncClient(timeout=30) as c:
        await c.post(DISCORD_WEBHOOK, json={"content": text[:1900]})


async def send_to_trainers(en: str, kr: str):
    msg = f"📢 **Notice**\n{en}\n\n[원문/한글]\n{kr}"
    if cards_enabled():
        await post(REQUESTS_CH, {"embeds": [{"title": "📢 Notice", "description": en[:4000], "color": 0xF1C40F,
                                             "fields": [{"name": "원문/한글", "value": kr[:1024]}]}],
                                 "allowed_mentions": {"parse": []}})
    else:
        await discord(msg)

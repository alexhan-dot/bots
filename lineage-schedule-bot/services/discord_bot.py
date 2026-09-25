"""Discord 매니저 봇 — HTTP Interactions (슬래시 명령 + 버튼 + 자동완성). 영어 UI.

흐름: /ot staff:Reno hours:2 → 메모리 인덱스에서 그날 Reno 시프트 찾기 (ms) → 미리보기 + [Confirm] [Cancel] (본인만 보임)
      → Confirm → 즉시 "Saving…" 응답 → 백그라운드로 시트 기록 → 메시지 갱신 + 채널에 공개 기록 한 줄
디스코드는 3초 안에 응답해야 하므로: 조회는 메모리, 시트 쓰기·AI 호출은 응답 후 백그라운드.
(Cloud Run 은 --no-cpu-throttling 이어야 응답 후 작업이 끊기지 않음 → deploy.sh 참고)
"""
import os, json, logging
import httpx
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
from services import index, manager, state, sheets
from services.manager import Draft, TITLES

log = logging.getLogger("discord")
API = "https://discord.com/api/v10"
PUBLIC_KEY = os.environ.get("DISCORD_PUBLIC_KEY", "")
APP_ID = os.environ.get("DISCORD_APP_ID", "")
MANAGER_ROLES = {x.strip() for x in os.environ.get("DISCORD_MANAGER_ROLE_IDS", "").split(",") if x.strip()}
EPHEMERAL = 64
GREEN, RED, GREY = 3, 4, 2

# ── 명령 정의 (tools/register_discord_commands.py 가 등록) ──
S, I, N = 3, 4, 10                      # option types: string, integer, number
def _o(name, typ, desc, required=False, auto=False):
    o = {"name": name, "type": typ, "description": desc, "required": required}
    if auto: o["autocomplete"] = True
    return o

DATE = _o("date", S, "YYYY-MM-DD, MM-DD, today, yesterday (default: today)")
COMMANDS = [
    {"name": "ot", "description": "Log overtime", "options": [
        _o("staff", S, "Employee", True, True), _o("hours", N, "OT hours", True), DATE,
        _o("time", S, "OT time range, e.g. 24:00-02:00"), _o("account", S, "Account/character", auto=True),
        _o("reason", S, "Reason")]},
    {"name": "incentive", "description": "Log an incentive / performance pay", "options": [
        _o("staff", S, "Employee", True, True), _o("amount", N, "Amount", True), DATE,
        _o("account", S, "Account/character", auto=True), _o("kpi", S, "KPI achieved, e.g. 7.5%"),
        _o("level", S, "Level band, e.g. 45-50"), _o("reason", S, "Reason")]},
    {"name": "penalty", "description": "Log a death penalty", "options": [
        _o("staff", S, "Player", True, True), _o("hours", N, "Penalty hours", True), DATE,
        _o("account", S, "Character", auto=True), _o("action", S, "Action taken"), _o("ir", S, "Incident report")]},
    {"name": "assign", "description": "Assign a player to a shift", "options": [
        _o("account", S, "Account/character", True, True), _o("player", S, "Player", True, True), DATE,
        _o("slot", I, "Shift slot number"), _o("time", S, "Shift time, e.g. 16:00-24:00")]},
    {"name": "off", "description": "Set a shift OFF", "options": [
        _o("account", S, "Account/character", True, True), DATE,
        _o("slot", I, "Shift slot (default: all shifts that day)"), _o("time", S, "Shift time")]},
    {"name": "extend", "description": "Change a shift's time", "options": [
        _o("account", S, "Account/character", True, True), _o("new_time", S, "New time, e.g. 24:00-09:30", True),
        DATE, _o("time", S, "Current shift time"), _o("slot", I, "Shift slot")]},
    {"name": "schedule", "description": "Show shifts for an account or a staff member", "options": [
        _o("account", S, "Account/character", auto=True), _o("staff", S, "Employee", auto=True), DATE]},
    {"name": "log", "description": "Free-text note — AI fills the form, you confirm", "options": [
        _o("text", S, "e.g. Reno 2h OT on Jjuni last night, boss fight", True)]},
    {"name": "week", "description": "Create next week's schedule rows now"},
]


# ── 서명 검증 ─────────────────────────────────
def verify(raw: bytes, signature: str, timestamp: str) -> bool:
    if not (PUBLIC_KEY and signature and timestamp):
        return False
    try:
        VerifyKey(bytes.fromhex(PUBLIC_KEY)).verify(timestamp.encode() + raw, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False


def _who(p: dict) -> tuple[str, str]:
    m = p.get("member") or {}
    u = m.get("user") or p.get("user") or {}
    return str(u.get("id", "")), m.get("nick") or u.get("global_name") or u.get("username") or "unknown"


def _msg(content: str, ephemeral=True, **extra) -> dict:
    return {"type": 4, "data": {"content": content, "flags": EPHEMERAL if ephemeral else 0,
                                "allowed_mentions": {"parse": []}, **extra}}


# ── 진입점 ────────────────────────────────────
async def handle(p: dict, bg) -> dict:
    t = p.get("type")
    if t == 1:                                                   # PING
        return {"type": 1}
    uid, name = _who(p)
    roles = set((p.get("member") or {}).get("roles", []))
    if MANAGER_ROLES and not roles & MANAGER_ROLES:
        return _msg("Only managers can use this bot.") if t != 4 else {"type": 8, "data": {"choices": []}}
    if t == 4:
        return _autocomplete(p["data"])
    if t == 2:
        return await _command(p, uid, name, bg)
    if t == 3:
        return _component(p, uid, name, bg)
    return _msg("Unsupported interaction.")


def _opts(data: dict) -> dict:
    return {o["name"]: o.get("value") for o in data.get("options", [])}


def _autocomplete(data: dict) -> dict:
    focused = next((o for o in data.get("options", []) if o.get("focused")), None)
    if not focused:
        return {"type": 8, "data": {"choices": []}}
    snap = index.get()
    pool = index.account_names(snap) if focused["name"] == "account" else snap.staff
    names = index.suggest(pool, str(focused.get("value", "")), 25)
    return {"type": 8, "data": {"choices": [{"name": n[:100], "value": n[:100]} for n in names]}}


async def _command(p, uid, name, bg) -> dict:
    cmd, o = p["data"]["name"], _opts(p["data"])
    if not index.ready():
        index.get(block=True)                                    # 콜드 스타트 첫 요청만 (보통 기동 시 warm)
    if cmd == "schedule":
        return _msg(_schedule_text(o))
    if cmd == "week":
        bg.add_task(_run_week, p["token"])
        return {"type": 5, "data": {"flags": EPHEMERAL}}
    if cmd == "log":
        bg.add_task(_run_log, p["token"], o.get("text", ""), uid)
        return {"type": 5, "data": {"flags": EPHEMERAL}}         # AI 파싱은 3초를 넘길 수 있어 먼저 "생각 중…"
    draft = manager.build(cmd, **o)
    sid = state.create(flow="discord", draft=draft.to_dict(), user_id=uid, status="preview")
    return {"type": 4, "data": {**render(draft, sid), "flags": EPHEMERAL}}


def _component(p, uid, name, bg) -> dict:
    action, sid = p["data"]["custom_id"].split("|", 1)
    s = state.get(sid)
    if not s:
        return {"type": 7, "data": {"content": "This request expired. Run the command again.", "embeds": [], "components": []}}
    if s.get("user_id") != uid:
        return _msg("Only the person who started this can confirm it.")
    draft = Draft.from_dict(s["draft"])
    if action == "no":
        state.delete(sid)
        return {"type": 7, "data": {"content": "Cancelled.", "embeds": [], "components": []}}
    if action == "pick":
        manager.select(draft, int(p["data"]["values"][0]))
        state.update(sid, draft=draft.to_dict())
        return {"type": 7, "data": render(draft, sid)}
    if action == "skip":                                         # 매칭 시프트 없이 저장
        draft.candidates = []
        draft.warnings.append("Saving without a matched shift.")
        state.update(sid, draft=draft.to_dict())
        return {"type": 7, "data": render(draft, sid)}
    if action == "ok":
        if s.get("status") != "preview" or not draft.can_save:  # 중복 클릭 방지
            return _msg("Already saved or not ready.")
        state.update(sid, status="saving")
        bg.add_task(_run_commit, p["token"], sid, draft, name)
        return {"type": 7, "data": {"content": "⏳ Saving…", "embeds": render(draft, sid)["embeds"], "components": []}}
    return _msg("Unknown action.")


# ── 화면 ─────────────────────────────────────
def render(d: Draft, sid: str) -> dict:
    desc = "\n".join([f"❌ {e}" for e in d.errors] + [f"⚠️ {w}" for w in d.warnings])
    if d.candidates and not d.selected:
        desc += ("\n" if desc else "") + "👇 Pick the shift this is about."
    embed = {"title": f"{TITLES[d.kind]} — confirm", "color": 0xE67E22 if d.errors or d.warnings else 0x2ECC71,
             "description": desc[:4000],
             "fields": [{"name": k, "value": str(v)[:1024], "inline": k not in ("Matched shift", "Reason", "Action")}
                        for k, v in manager.preview_lines(d)]}
    rows = []
    if d.candidates:
        rows.append({"type": 1, "components": [{
            "type": 3, "custom_id": f"pick|{sid}", "placeholder": "Select shift",
            "options": [{"label": manager._shift_text(c)[:100], "value": str(i),
                         "default": bool(d.selected and d.selected[0] == c)}
                        for i, c in enumerate(d.candidates[:25])]}]})
    buttons = [{"type": 2, "style": GREEN, "label": "Confirm", "custom_id": f"ok|{sid}", "disabled": not d.can_save},
               {"type": 2, "style": RED, "label": "Cancel", "custom_id": f"no|{sid}"}]
    if d.candidates and not d.selected and d.kind in ("ot", "incentive", "penalty"):
        buttons.insert(1, {"type": 2, "style": GREY, "label": "Save without shift", "custom_id": f"skip|{sid}"})
    rows.append({"type": 1, "components": buttons})
    return {"content": "", "embeds": [embed], "components": rows, "allowed_mentions": {"parse": []}}


def _schedule_text(o: dict) -> str:
    date = manager.parse_date(o.get("date"))
    if not date:
        return "Can't read the date."
    snap = index.get()
    if o.get("account"):
        acc, cands = index.resolve_account(o["account"], snap)
        if not acc:
            return f"Unknown account. Did you mean: {', '.join(cands) or '—'}?"
        rows = index.shifts_on(date, acc, snap=snap)
        head = f"**{acc}** — {date}"
    elif o.get("staff"):
        who, _ = index.resolve_name(snap.staff, o["staff"])
        who = who or o["staff"]
        rows = index.shifts_on(date, player=who, snap=snap)
        tl = index.tl_on(date, who, snap)
        head = f"**{who}** — {date}" + (f"\nTL shift: {tl[0].shift} ({tl[0].attendance or 'no attendance yet'})" if tl else "")
    else:
        return "Give an account or a staff name."
    body = "\n".join(f"• #{s.slot} `{'OFF' if s.off else s.time}` {s.player or '—'}"
                     + (f" @{s.ground}" if s.ground else "") + ("" if o.get("account") else f" ({s.account})")
                     for s in rows)
    return f"{head}\n{body or 'No shifts.'}"


# ── 백그라운드 작업 (응답 후) ──────────────────
async def _edit(token: str, data: dict):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.patch(f"{API}/webhooks/{APP_ID}/{token}/messages/@original", json=data)
        if r.status_code >= 300:
            log.warning("discord edit failed %s %s", r.status_code, r.text[:200])


async def _followup(token: str, content: str, ephemeral=False):
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post(f"{API}/webhooks/{APP_ID}/{token}",
                     json={"content": content, "flags": EPHEMERAL if ephemeral else 0,
                           "allowed_mentions": {"parse": []}})


async def _run_commit(token: str, sid: str, draft: Draft, manager_name: str):
    try:
        summary = manager.commit(draft, manager_name)
        state.update(sid, status="done")
        await _edit(token, {"content": f"✅ Saved: {summary}", "components": []})
        await _followup(token, f"📝 {summary} · by {manager_name}")        # 채널 공개 기록
    except Exception as e:
        log.exception("commit failed")
        state.update(sid, status="preview")                                   # 다시 시도 가능
        await _edit(token, {**render(draft, sid), "content": f"❌ Not saved: {e}"})


async def _run_log(token: str, text: str, uid: str):
    from services import ai_parse                                             # anthropic 은 /log 에서만 로드
    try:
        fields = await ai_parse.parse(text)
        kind = fields.pop("kind")
        draft = manager.build(kind, **fields)
        sid = state.create(flow="discord", draft=draft.to_dict(), user_id=uid, status="preview")
        await _edit(token, {**render(draft, sid), "content": f"> {text[:300]}"})
    except Exception as e:
        log.exception("log parse failed")
        await _edit(token, {"content": f"❌ Couldn't read that note ({e}). Try the form commands: /ot /incentive /penalty /assign /off /extend"})


async def _run_week(token: str):
    try:
        n = sheets.rollover()
        index.invalidate()
        await _edit(token, {"content": f"✅ Next week: {n} rows created." if n else "Next week already exists."})
    except Exception as e:
        await _edit(token, {"content": f"❌ Failed: {e}"})

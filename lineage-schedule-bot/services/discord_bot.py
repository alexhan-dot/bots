"""Discord 매니저 봇 — HTTP Interactions (슬래시 명령 + 버튼 + 자동완성). 영어 UI.

흐름: /ot staff:Reno hours:2 → 메모리 인덱스에서 그날 Reno 시프트 찾기 (ms) → 미리보기 + [Confirm] [Cancel] (본인만 보임)
      → Confirm → 즉시 "Saving…" 응답 → 백그라운드로 시트 기록 → 메시지 갱신 + 채널에 공개 기록 한 줄
디스코드는 3초 안에 응답해야 하므로: 조회는 메모리, 시트 쓰기·AI 호출은 응답 후 백그라운드.
(Cloud Run 은 --no-cpu-throttling 이어야 응답 후 작업이 끊기지 않음 → deploy.sh 참고)
"""
import os, json, logging, datetime
import httpx
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
from services import index, manager, state, sheets, notify, clock
from services.manager import Draft, TITLES

log = logging.getLogger("discord")
API = "https://discord.com/api/v10"
PUBLIC_KEY = os.environ.get("DISCORD_PUBLIC_KEY", "")
APP_ID = os.environ.get("DISCORD_APP_ID", "")
MANAGER_ROLES = {x.strip() for x in os.environ.get("DISCORD_MANAGER_ROLE_IDS", "").split(",") if x.strip()}
EPHEMERAL = 64
PLAYER_CMDS = {"iam", "shot", "myshifts"}                    # 매니저 역할 없이도 쓰는 명령 (플레이어용)
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
    {"name": "schedule", "description": "Today's board (gaps first), or one account / staff member", "options": [
        _o("account", S, "Account/character", auto=True), _o("staff", S, "Employee", auto=True), DATE]},
    {"name": "log", "description": "Free-text note — AI fills the form, you confirm", "options": [
        _o("text", S, "e.g. Reno 2h OT on Jjuni last night, boss fight", True)]},
    {"name": "week", "description": "Create next week's schedule rows now"},
    {"name": "iam", "description": "Players: link your Discord to your name in the schedule (once)", "options": [
        _o("name", S, "Your name as in the schedule", True, True)]},
    {"name": "shot", "description": "Players: upload your START or END screenshot (EXP % and Adena are read)", "options": [
        {"name": "image", "type": 11, "description": "Game screenshot (EXP bar + inventory with Adena)", "required": True},
        _o("account", S, "Character (default: your current shift)", auto=True),
        {"name": "kind", "type": S, "description": "Default: start if not started yet, else end",
         "choices": [{"name": "Start of shift", "value": "start"}, {"name": "End of shift", "value": "end"}]}]},
    {"name": "myshifts", "description": "Players: your shifts for the next 3 days"},
    {"name": "check", "description": "Post a confirmation card now (daily or weekly schedule check)", "options": [
        {"name": "kind", "type": S, "description": "day or week", "required": True,
         "choices": [{"name": "Today / a day", "value": "day"}, {"name": "Next week", "value": "week"}]},
        _o("date", S, "Day (default today) or any date in the week (default next week)")]},
    {"name": "plan", "description": "Apply a schedule for a date range (Planner tab, or one rule here)", "options": [
        _o("from", S, "First date, e.g. 10-01 (default: each Planner row's From)"),
        _o("to", S, "Last date, e.g. 10-31 (default: each Planner row's To)"),
        _o("account", S, "Only this account — or, with time/player, a one-off rule", auto=True),
        _o("slot", I, "Shift slot (default 1)"), _o("days", S, "Daily, Mon-Fri, Weekends, Mon,Wed,Fri (default Daily)"),
        _o("time", S, "Shift time, e.g. 16:00-24:00, 4pm-12am, OFF"), _o("player", S, "Player", auto=True),
        _o("ground", S, "Hunting ground"), _o("reapply", 5, "Also re-apply Planner rows already applied")]},
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
    data = p.get("data") or {}
    player_ok = (data.get("name") in PLAYER_CMDS if t in (2, 4)
                 else str(data.get("custom_id", "")).startswith("shot_"))
    if MANAGER_ROLES and not roles & MANAGER_ROLES and not player_ok:
        return _msg("Only managers can use this bot.") if t != 4 else {"type": 8, "data": {"choices": []}}
    if t == 4:
        return _autocomplete(p["data"])
    if t == 2:
        return await _command(p, uid, name, bg)
    if t == 3:
        return _component(p, uid, name, bg)
    if t == 5:
        return _modal(p, name, bg)
    return _msg("Unsupported interaction.")


def _opts(data: dict) -> dict:
    return {o["name"]: o.get("value") for o in data.get("options", [])}


def _autocomplete(data: dict) -> dict:
    """이름 + 그날 시프트를 같이 보여줌 → 매니저가 맞는 사람/계정을 바로 고름. 그날 근무자가 먼저"""
    opts = {o["name"]: o for o in data.get("options", [])}
    focused = next((o for o in opts.values() if o.get("focused")), None)
    if not focused:
        return {"type": 8, "data": {"choices": []}}
    snap = index.get()
    q = str(focused.get("value", ""))
    date = manager.parse_date((opts.get("date") or {}).get("value")) or clock.today().isoformat()
    day = [x for x in snap.shifts if x.date == date]
    if focused["name"] == "account":
        working = sorted({x.account for x in day if not x.off}, key=str.lower)
        pool = working + [a for a in index.account_names(snap) if a not in set(working)]
        names = index.suggest(pool, q, 25)
        label = lambda a: a + ("  ·  " + "  ".join(
            f"#{x.slot} {x.time[:5]} {x.player or '—'}"
            for x in sorted((x for x in day if x.account == a and not x.off), key=lambda x: x.slot))
            if any(x.account == a and not x.off for x in day) else "")
    else:
        by = {}
        for x in day:
            if x.player and not x.off: by.setdefault(x.player, []).append(x)
        tl = {t.leader: t for t in snap.tl if t.date == date}
        pool = sorted(set(by) | set(tl), key=str.lower) + [n for n in snap.staff if n not in by and n not in tl]
        names = index.suggest(pool, q, 25)
        def label(n):
            parts = [f"{x.account} #{x.slot} {x.time}" for x in by.get(n, [])]
            if n in tl: parts.append(f"TL {tl[n].shift}")
            return n + ("  ·  " + ", ".join(parts) if parts else "")
    return {"type": 8, "data": {"choices": [{"name": label(n)[:100], "value": n[:100]} for n in names]}}


async def _command(p, uid, name, bg) -> dict:
    cmd, o = p["data"]["name"], _opts(p["data"])
    if not index.ready():
        index.get(block=True)                                    # 콜드 스타트 첫 요청만 (보통 기동 시 warm)
    if cmd == "schedule":
        out = _schedule_text(o)
        return _msg(out) if isinstance(out, str) else {"type": 4, "data": {**out, "flags": EPHEMERAL,
                                                                           "allowed_mentions": {"parse": []}}}
    if cmd == "week":
        bg.add_task(_run_week, p["token"])
        return {"type": 5, "data": {"flags": EPHEMERAL}}
    if cmd == "iam":
        return _iam(uid, name, o.get("name", ""), bg)
    if cmd == "myshifts":
        return _msg(_myshifts(uid))
    if cmd == "shot":
        att = ((p["data"].get("resolved") or {}).get("attachments") or {}).get(str(o.get("image")), {})
        bg.add_task(_run_shot, p["token"], uid, att, o.get("account"), o.get("kind"))
        return {"type": 5, "data": {"flags": EPHEMERAL}}
    if cmd == "check":
        bg.add_task(_run_check, p["token"], o.get("kind", "day"), o.get("date"))
        return {"type": 5, "data": {"flags": EPHEMERAL}}
    if cmd == "plan":
        bg.add_task(_run_plan_preview, p["token"], _plan_inputs(o), uid)
        return {"type": 5, "data": {"flags": EPHEMERAL}}         # 시트 읽기 → 미리보기는 백그라운드
    if cmd == "log":
        bg.add_task(_run_log, p["token"], o.get("text", ""), uid)
        return {"type": 5, "data": {"flags": EPHEMERAL}}         # AI 파싱은 3초를 넘길 수 있어 먼저 "생각 중…"
    draft = manager.build(cmd, **o)
    sid = state.create(flow="discord", draft=draft.to_dict(), user_id=uid, status="preview")
    return {"type": 4, "data": {**render(draft, sid), "flags": EPHEMERAL}}


def _component(p, uid, name, bg) -> dict:
    action, sid = p["data"]["custom_id"].split("|", 1)
    if action.startswith("req_"):                                # 텔레그램 영업 요청 카드 버튼 (누구나 매니저면 처리)
        return _request_component(action, sid, name, bg)
    if action.startswith("shot_"):                               # 플레이어 스크린샷 미리보기
        return _shot_component(p, action, sid, uid, name, bg)
    if action.startswith("conf_"):                               # 일별·주별 컨펌 카드
        return _confirm_component(p, action, sid, name, bg)
    s = state.get(sid)
    if not s:
        return {"type": 7, "data": {"content": "This request expired. Run the command again.", "embeds": [], "components": []}}
    if s.get("user_id") != uid:
        return _msg("Only the person who started this can confirm it.")
    if s.get("flow") == "plan":
        return _plan_component(p, action, sid, s, name, bg)
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


def _schedule_text(o: dict) -> str | dict:
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
        return _overview(date, snap)
    rows = [x for x in rows if not x.off]
    body = "\n".join(f"• #{s.slot} `{s.time}` {s.player or '—'}"
                     + (f" @{s.ground}" if s.ground else "") + ("" if o.get("account") else f" ({s.account})")
                     for s in rows)
    return f"{head}\n{body or 'No shifts.'}"


def _visible(x) -> bool:
    """보드에 보일 시프트: OFF 제외, 파밍 계정은 플레이어가 배정된 것만"""
    return not x.off and (x.type != "Farming" or bool(x.player))


def _overview(date: str, snap) -> dict:
    """/schedule (인자 없음) — 그날 전체를 임베드로 (빈 자리 / 고객 / 농장 / TL). 메시지 총 6000자 제한 안에서"""
    day = sorted((x for x in snap.shifts if x.date == date and _visible(x)), key=lambda x: (x.time, x.account.lower()))
    if not day:
        return {"content": f"No shifts on {date}."}
    gaps = [x for x in day if not x.player]                     # 플레이어 없는 고객 시프트
    embeds = [{"title": f"📋 {date} — {len(day)} shifts · {len(gaps)} without a player",
               "color": 0xE67E22 if gaps else 0x2ECC71,
               "description": ("⚠️ " + ", ".join(f"{x.account} #{x.slot} `{x.time}`" for x in gaps)) if gaps else "✅ Every shift has a player"}]
    for typ, color in (("Client", 0x3498DB), ("Farming", 0x27AE60)):
        rows = [x for x in day if x.type == typ]
        lines = [f"`{x.time}` **{x.account}** #{x.slot} · {x.player or '⚠️ no player'}" + (f" @{x.ground}" if x.ground else "")
                 for x in rows]
        if lines:
            embeds.append({"title": f"{typ} ({len(lines)})", "color": color, "description": "\n".join(lines)})
    tl = [t for t in snap.tl if t.date == date]
    if tl:
        embeds.append({"title": "Team leaders", "color": 0x95A5A6,
                       "description": " · ".join(f"{t.leader} {t.shift}" for t in tl)})
    budget = 5800                                                  # 임베드 전체 6000자 제한
    for e in embeds:
        room = max(0, min(4000, budget - len(e["title"])))
        if len(e["description"]) > room:
            e["description"] = e["description"][:max(0, room - 45)] + "\n… /schedule account:<name> for more"
        budget -= len(e["title"]) + len(e["description"])
    return {"content": "", "embeds": embeds}


# ── 텔레그램 영업 요청 카드 (notify.request_card) ──
def _request_component(action: str, sid: str, name: str, bg) -> dict:
    s = state.get(sid)
    if not s:
        return _msg("This request expired.")
    op = s.get("op", {})
    card = lambda status, open_=True: notify.request_card(sid, op, s.get("en", ""), s.get("sheet_result", ""),
                                                           s.get("sales_name", ""), status, open_)
    if action == "req_sched":
        acc = op.get("character") or ""
        snap = index.get()
        start = clock.today()
        days = []
        for i in range(7):
            d = (start + datetime.timedelta(days=i)).isoformat()
            rows = [x for x in index.shifts_on(d, acc, snap=snap) if not x.off]
            days.append(f"`{d[5:]}` " + (" · ".join(f"#{x.slot} {x.time} {x.player or '⚠️'}" for x in rows) or "OFF"))
        return _msg(f"**{acc}** — next 7 days\n" + ("\n".join(days) or "No shifts."))
    if action == "req_ok":
        if s.get("status") == "done":
            return _msg(f"Already handled{' by ' + s['handled_by'] if s.get('handled_by') else ''}.")
        state.update(sid, status="done", handled_by=name)
        bg.add_task(_tell_sales, s, "ok", name, "")
        verb = "Done" if op.get("type") == "RELOGIN" else "Confirmed"
        return {"type": 7, "data": card(f"✅ {verb} by {name} · {clock.stamp()[11:16]}", open_=False)}
    if action == "req_reply":
        q = op.get("question") or ""
        return {"type": 9, "data": {"custom_id": f"req_modal|{sid}", "title": "Reply to sales (English)",
                "components": [{"type": 1, "components": [{
                    "type": 4, "custom_id": "text", "style": 2, "required": True, "max_length": 1000,
                    "label": ("Answer: " + q)[:45] if q else "Message (sent in Korean)",
                    "placeholder": "e.g. Abyss gives more EXP after level 60"}]}]}}
    return _msg("Unknown action.")


def _modal(p, name: str, bg) -> dict:
    cid = p["data"]["custom_id"]
    action, sid = cid.split("|", 1)
    if action == "shot_modal":
        return _shot_modal(p, sid)
    if action != "req_modal":
        return _msg("Unknown form.")
    s = state.get(sid)
    if not s:
        return _msg("This request expired.")
    text = p["data"]["components"][0]["components"][0]["value"].strip()
    op = s.get("op", {})
    question = op.get("type") == "QUESTION"
    state.update(sid, status="done" if question else s.get("status"), handled_by=name, answer=text)
    bg.add_task(_tell_sales, s, "reply", name, text)
    card = notify.request_card(sid, op, s.get("en", ""), s.get("sheet_result", ""), s.get("sales_name", ""),
                               f"💬 {name}: {text}"[:1000], open_=not question)
    return {"type": 7, "data": card} if p.get("message") else _msg("Sent to sales.")


async def _tell_sales(s: dict, kind: str, manager_name: str, text: str):
    """매니저 버튼/답장 → 영업자 텔레그램"""
    from services import telegram as tg, parser, translate
    chat = s.get("sales_chat_id")
    if not chat:
        return
    op = s.get("op", {})
    try:
        if kind == "ok":
            msg = ("✅ 재접속 처리 완료" if op.get("type") == "RELOGIN" else "✅ 확정 완료") + \
                  f"\n{parser.summarize_kr(op)}\n(매니저: {manager_name})"
        else:
            kr = await translate.en_to_kr(text)
            head = f"💬 매니저 답변\n질문: {op.get('question')}\n" if op.get("type") == "QUESTION" else "💬 매니저 메시지\n"
            msg = f"{head}{kr}\n(EN: {text})"
        await tg.send(chat, msg)
    except Exception:
        log.exception("telegram relay failed")


# ── 플레이어: /iam · /myshifts · /shot ─────────────
def _iam(uid: str, discord_name: str, raw: str, bg) -> dict:
    snap = index.get()
    who, cands = index.resolve_name(snap.staff, raw)
    if not who:
        return _msg(f"Can't find '{raw}' in the schedule." + (f" Did you mean: {', '.join(cands)}?" if cands else "")
                    + " Pick your name from the list.")
    snap.links[str(uid)] = who                                     # 바로 쓸 수 있게 메모리에도
    bg.add_task(_run_link, uid, discord_name, who)
    return _msg(f"✅ Linked: you are **{who}**. Now use `/shot` at the start and end of each shift.")


async def _run_link(uid, discord_name, who):
    from services import reports
    try:
        reports.link(uid, discord_name, who)
    except Exception:
        log.exception("link failed")


def _myshifts(uid: str) -> str:
    snap = index.get()
    who = snap.links.get(str(uid))
    if not who:
        return "First link yourself: `/iam name:<your name>`"
    out = []
    for i in range(3):
        d = (clock.today() + datetime.timedelta(days=i)).isoformat()
        rows = [x for x in index.shifts_on(d, player=who, snap=snap) if not x.off]
        out += [f"`{d[5:]}` `{x.time}` **{x.account}** #{x.slot}" + (f" @{x.ground}" if x.ground else "") for x in rows]
    return f"**{who}** — next 3 days\n" + ("\n".join(out) or "No shifts.")


def _fmt_vals(v: dict) -> tuple[str, str, str]:
    lv = str(v.get("level")) if (v.get("level") or 0) > 0 else "?"
    pct = f"{v['exp_percent']:.4f}%" if isinstance(v.get("exp_percent"), (int, float)) and v["exp_percent"] >= 0 else "?"
    ad = f"{int(v['adena']):,}" if isinstance(v.get("adena"), (int, float)) and v["adena"] >= 0 else "?"
    return lv, pct, ad


def _shot_render(sid: str, st: dict) -> dict:
    from services import reports
    v, sh, kind = st["vals"], st["shift"], st["kind"]
    lv, pct, ad = _fmt_vals(v)
    fields = [{"name": "Shift", "value": f"{sh['date']} · {sh['account']} #{sh['slot']} `{sh['time']}`", "inline": False},
              {"name": "Level", "value": lv, "inline": True}, {"name": "EXP", "value": pct, "inline": True},
              {"name": "Adena", "value": ad, "inline": True}]
    if kind == "end" and st.get("start"):
        exp, adena = reports.gains(st["start"], v)
        gain = []
        if exp is not None: gain.append(f"EXP **{exp:+.4f}%**")
        if adena is not None: gain.append(f"Adena **{adena:+,}**")
        fields.append({"name": "This shift", "value": " · ".join(gain) or "— (start numbers missing)", "inline": False})
    warn = []
    if "?" in (lv, pct, ad): warn.append("⚠️ Some numbers weren't readable — press **Fix** to type them.")
    if v.get("confidence") == "low": warn.append("⚠️ Low confidence — please double-check.")
    if v.get("notes"): warn.append(f"🛈 {v['notes'][:200]}")
    embed = {"title": f"📸 {'Start' if kind == 'start' else 'End'} of shift — {st['player']}",
             "description": "\n".join(warn) or "Check the numbers, then **Confirm**.",
             "color": 0xE67E22 if warn else 0x2ECC71, "fields": fields,
             "image": {"url": st["url"]} if st.get("url") else None}
    embed = {k: v2 for k, v2 in embed.items() if v2 is not None}
    other = "end" if kind == "start" else "start"
    rows = [{"type": 1, "components": [
        {"type": 2, "style": GREEN, "label": "Confirm", "custom_id": f"shot_ok|{sid}"},
        {"type": 2, "style": GREY, "label": "Fix numbers", "custom_id": f"shot_fix|{sid}"},
        {"type": 2, "style": GREY, "label": f"It's the {other}", "custom_id": f"shot_kind|{sid}"},
        {"type": 2, "style": RED, "label": "Cancel", "custom_id": f"shot_no|{sid}"}]}]
    return {"content": "", "embeds": [embed], "components": rows, "allowed_mentions": {"parse": []}}


async def _run_shot(token: str, uid: str, att: dict, account: str | None, kind: str | None):
    from services import vision, reports
    try:
        snap = index.get(block=True)
        who = snap.links.get(str(uid))
        if not who:
            await _edit(token, {"content": "First link yourself: `/iam name:<your name>` — then send the screenshot again."}); return
        acc = None
        if account:
            acc, cands = index.resolve_account(account, snap)
            if not acc:
                await _edit(token, {"content": f"Unknown character '{account}'."}); return
        shift, near = reports.find_shift(who, acc, snap=snap)
        if not shift:
            hint = ", ".join(f"{s.date[5:]} {s.account} `{s.time}`" for s in near[:5]) or "none today/yesterday"
            await _edit(token, {"content": f"❌ No shift of yours is running now. Your shifts: {hint}. "
                                           "Add `account:` if you're on a different character."}); return
        sh = manager._sd(shift)
        async with httpx.AsyncClient(timeout=30) as c:
            img = (await c.get(att["url"])).content
        vals = await vision.read_screenshot(img, (att.get("content_type") or "").split(";")[0])
        k = kind or reports.kind_for(sh, who)
        start = None
        if k == "end":
            _, rep = reports.find_report(sh, who)
            if rep:
                start = {"level": rep.get("Start Lv"), "exp_percent": rep.get("Start EXP %"), "adena": rep.get("Start Adena")}
        st = {"player": who, "shift": sh, "kind": k, "vals": vals, "url": att.get("url"), "filename": att.get("filename") or "shot.png",
              "ctype": att.get("content_type") or "image/png", "start": start}
        sid = state.create(flow="shot", user_id=uid, status="preview", **st)
        await _edit(token, _shot_render(sid, st))
    except Exception as e:
        log.exception("shot failed")
        await _edit(token, {"content": f"❌ Couldn't read the screenshot: {e}"})


def _shot_component(p, action: str, sid: str, uid: str, name: str, bg) -> dict:
    st = state.get(sid)
    if not st:
        return {"type": 7, "data": {"content": "This expired. Send `/shot` again.", "embeds": [], "components": []}}
    if st.get("user_id") != uid:
        return _msg("Only the player who sent it can confirm.")
    if action == "shot_no":
        state.delete(sid)
        return {"type": 7, "data": {"content": "Cancelled.", "embeds": [], "components": []}}
    if action == "shot_kind":
        st["kind"] = "end" if st["kind"] == "start" else "start"
        if st["kind"] == "end" and not st.get("start"):
            from services import reports
            _, rep = reports.find_report(st["shift"], st["player"])
            if rep:
                st["start"] = {"level": rep.get("Start Lv"), "exp_percent": rep.get("Start EXP %"), "adena": rep.get("Start Adena")}
        state.update(sid, kind=st["kind"], start=st.get("start"))
        return {"type": 7, "data": _shot_render(sid, st)}
    if action == "shot_fix":
        lv, pct, ad = _fmt_vals(st["vals"])
        inp = lambda cid, label, val: {"type": 1, "components": [{"type": 4, "custom_id": cid, "style": 1, "label": label,
                                                                   "required": False, "value": "" if val == "?" else val.replace("%", "").replace(",", "")}]}
        return {"type": 9, "data": {"custom_id": f"shot_modal|{sid}", "title": "Fix the numbers", "components": [
            inp("level", "Level", lv), inp("exp", "EXP % (e.g. 37.4512)", pct), inp("adena", "Adena (e.g. 1234567)", ad)]}}
    if action == "shot_ok":
        if st.get("status") != "preview":
            return _msg("Already saved.")
        state.update(sid, status="saving")
        bg.add_task(_run_shot_save, p["token"], sid, st, uid)
        return {"type": 7, "data": {"content": "⏳ Saving…", "embeds": _shot_render(sid, st)["embeds"], "components": []}}
    return _msg("Unknown action.")


def _shot_modal(p, sid: str) -> dict:
    st = state.get(sid)
    if not st:
        return _msg("This expired. Send `/shot` again.")
    vals = dict(st["vals"])
    for row in p["data"]["components"]:
        c = row["components"][0]
        raw = str(c.get("value", "")).replace(",", "").replace("%", "").strip()
        if not raw: continue
        try:
            if c["custom_id"] == "level": vals["level"] = int(float(raw))
            if c["custom_id"] == "exp": vals["exp_percent"] = float(raw)
            if c["custom_id"] == "adena": vals["adena"] = int(float(raw))
        except ValueError:
            return _msg(f"'{raw}' is not a number.")
    vals["notes"] = "edited by player"; vals["confidence"] = "high"
    st["vals"] = vals
    state.update(sid, vals=vals)
    return {"type": 7, "data": _shot_render(sid, st)}


async def _run_shot_save(token: str, sid: str, st: dict, uid: str):
    from services import reports
    try:
        link = ""
        try:                                                     # 스크린샷을 #shift-reports 에 올려 영구 보관
            async with httpx.AsyncClient(timeout=30) as c:
                img = (await c.get(st["url"])).content
            lv, pct, ad = _fmt_vals(st["vals"])
            sh = st["shift"]
            caption = (f"📸 **{'Start' if st['kind'] == 'start' else 'End'}** · {st['player']} · {sh['account']} #{sh['slot']} "
                       f"`{sh['time']}` · Lv {lv} · {pct} · {ad} adena")
            if notify.BOT_TOKEN and notify.REPORTS_CH:
                msg = await notify.post_file(notify.REPORTS_CH, {"content": caption, "allowed_mentions": {"parse": []}},
                                             st["filename"], img, st["ctype"])
                if msg.get("id"):
                    link = f"https://discord.com/channels/{os.environ.get('DISCORD_GUILD_ID', '@me')}/{notify.REPORTS_CH}/{msg['id']}"
        except Exception:
            log.exception("screenshot repost failed")
        out = reports.save(st["shift"], st["player"], st["kind"], st["vals"], link or st.get("url", ""), uid)
        state.update(sid, status="done")
        res = f"✅ Saved {'start' if out['kind'] == 'start' else 'end'} of shift."
        if out["kind"] == "end":
            parts = []
            if out["exp_gain"] is not None: parts.append(f"EXP {out['exp_gain']:+.4f}%")
            if out["adena_gain"] is not None: parts.append(f"Adena {out['adena_gain']:+,}")
            res += " " + (" · ".join(parts) if parts else "(no start numbers to compare)")
            await notify.log_line(f"📈 {st['player']} {st['shift']['account']} {st['shift']['date']}: " + (" · ".join(parts) or "end saved"))
        await _edit(token, {"content": res, "components": []})
    except Exception as e:
        log.exception("shot save failed")
        state.update(sid, status="preview")
        await _edit(token, {**_shot_render(sid, st), "content": f"❌ Not saved: {e}"})


# ── 일별·주별 컨펌 ──────────────────────────────
def _confirm_component(p, action: str, period: str, name: str, bg) -> dict:
    from services import confirm
    kind = "week" if action.endswith("week") else "day"
    if action.startswith("conf_refresh"):
        embed = confirm.day_card(period) if kind == "day" else confirm.week_card(period)
        return {"type": 7, "data": confirm.card_payload(kind, period, embed)}
    old = ((p.get("message") or {}).get("embeds") or [{}])[0]
    bg.add_task(_run_confirm, kind, period, name)
    return {"type": 7, "data": {**confirm.card_payload(kind, period, old, done_by=name), "content": ""}}


async def _run_confirm(kind: str, period: str, name: str):
    from services import confirm
    try:
        confirm.confirm(kind, period, name)
        line = f"✅ {'Day' if kind == 'day' else 'Week'} {period} confirmed by {name}"
        await notify.log_line(line)
    except Exception:
        log.exception("confirm failed")


async def _run_check(token: str, kind: str, date: str | None):
    from services import confirm
    try:
        d = manager.parse_date(date) if date else None
        if kind == "week":
            base = datetime.date.fromisoformat(d) if d else clock.today() + datetime.timedelta(days=7)
            period = sheets.week_start(base).isoformat()
            sheets.rollover(datetime.date.fromisoformat(period))       # 없으면 지난주 복사(플레이어 유지)
            index.invalidate()
        else:
            period = d or clock.today().isoformat()
        ok = await confirm.post(kind, period)
        await _edit(token, {"content": f"✅ Posted the {kind} check for {period}." if ok else
                            "❌ No confirm channel. Run tools/setup_discord.py again and redeploy."})
    except Exception as e:
        log.exception("check failed")
        await _edit(token, {"content": f"❌ Failed: {e}"})


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
        line = f"📝 {summary} · by {manager_name}"
        if not await notify.log_line(line):                                  # 기록 채널 없으면 이 채널에 공개 기록
            await _followup(token, line)
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


# ── /plan (기간 스케줄) ─────────────────────────
def _plan_inputs(o: dict) -> dict:
    inline = None
    if o.get("time") or o.get("player") or o.get("ground"):             # 명령에 규칙을 직접 적은 경우
        inline = {"Account": o.get("account", ""), "Slot": o.get("slot") or 1, "Days": o.get("days") or "Daily",
                  "Time": o.get("time", ""), "Player": o.get("player", ""), "Hunting Ground": o.get("ground", ""),
                  "From": "", "To": ""}
    return {"inline": inline, "lo": o.get("from"), "hi": o.get("to"),
            "account": None if inline else o.get("account"), "reapply": bool(o.get("reapply"))}


def _norm_range(inp: dict) -> dict:
    inp = dict(inp)
    for k in ("lo", "hi"):
        if inp.get(k):
            inp[k] = manager.parse_date(inp[k]) or inp[k]
    return inp


def _plan_embed(plan, inp: dict, sid: str | None, done=False) -> dict:
    lines = [f"❌ {e}" for e in plan.errors] + [f"⚠️ {w}" for w in plan.warnings]
    if plan.maint_shifts:
        lines.append(f"⚙️ {plan.maint_shifts} shifts overlap maintenance ({plan.maint}) — "
                     f"{plan.maint_hours:g}h not counted as work")
    accounts = sorted({r.account for r in plan.rules})
    fields = [("Dates", f"{plan.lo} ~ {plan.hi}" if plan.lo else "—"),
              ("Rules", str(len(plan.rules))),
              ("Accounts", ", ".join(accounts)[:1024] or "—"),
              ("Updated shifts", str(plan.updates)), ("New shifts", str(plan.new)),
              ("Already same", str(plan.unchanged))]
    title = "Schedule plan — " + ("applied" if done else "confirm")
    embed = {"title": title, "color": 0xE67E22 if plan.errors or plan.warnings else 0x2ECC71,
             "description": "\n".join(lines)[:4000],
             "fields": [{"name": k, "value": v, "inline": k != "Accounts"} for k, v in fields]}
    rows = []
    if sid and not done:
        rows = [{"type": 1, "components": [
            {"type": 2, "style": GREEN, "label": f"Apply {plan.changes} changes", "custom_id": f"ok|{sid}",
             "disabled": plan.changes == 0},
            {"type": 2, "style": RED, "label": "Cancel", "custom_id": f"no|{sid}"}]}]
    return {"content": "", "embeds": [embed], "components": rows, "allowed_mentions": {"parse": []}}


async def _run_plan_preview(token: str, inp: dict, uid: str):
    from services import planner
    try:
        inp = _norm_range(inp)
        plan, _, _ = planner.build(**inp)
        sid = state.create(flow="plan", plan_inputs=inp, user_id=uid, status="preview") if plan.changes else None
        await _edit(token, _plan_embed(plan, inp, sid))
    except Exception as e:
        log.exception("plan preview failed")
        await _edit(token, {"content": f"❌ Couldn't build the plan: {e}"})


def _plan_component(p, action, sid, s, name, bg) -> dict:
    if action == "no":
        state.delete(sid)
        return {"type": 7, "data": {"content": "Cancelled.", "embeds": [], "components": []}}
    if action == "ok":
        if s.get("status") != "preview":
            return _msg("Already applied.")
        state.update(sid, status="saving")
        bg.add_task(_run_plan_apply, p["token"], sid, s["plan_inputs"], name)
        return {"type": 7, "data": {"content": "⏳ Applying…", "components": []}}
    return _msg("Unknown action.")


async def _run_plan_apply(token: str, sid: str, inp: dict, manager_name: str):
    from services import planner
    try:
        plan = planner.apply(**inp, by=manager_name)
        state.update(sid, status="done")
        index.invalidate()
        await _edit(token, {**_plan_embed(plan, inp, None, done=True), "content": "✅ Schedule updated."})
        line = (f"🗓️ Plan applied {plan.lo}~{plan.hi}: {plan.updates} updated, {plan.new} new "
                f"({', '.join(sorted({r.account for r in plan.rules}))[:1500]}) · by {manager_name}")
        if not await notify.log_line(line):
            await _followup(token, line)
    except Exception as e:
        log.exception("plan apply failed")
        state.update(sid, status="preview")
        await _edit(token, {"content": f"❌ Not applied: {e}"})


async def _run_week(token: str):
    try:
        n = sheets.rollover()
        index.invalidate()
        await _edit(token, {"content": f"✅ Next week: {n} rows created." if n else "Next week already exists."})
    except Exception as e:
        await _edit(token, {"content": f"❌ Failed: {e}"})

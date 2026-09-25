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
    if MANAGER_ROLES and not roles & MANAGER_ROLES:
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
            f"#{x.slot} {'OFF' if x.off else x.time[:5] + ' ' + (x.player or '—')}"
            for x in sorted((x for x in day if x.account == a), key=lambda x: x.slot)) if any(x.account == a for x in day) else "")
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
    body = "\n".join(f"• #{s.slot} `{'OFF' if s.off else s.time}` {s.player or '—'}"
                     + (f" @{s.ground}" if s.ground else "") + ("" if o.get("account") else f" ({s.account})")
                     for s in rows)
    return f"{head}\n{body or 'No shifts.'}"


def _overview(date: str, snap) -> dict:
    """/schedule (인자 없음) — 그날 전체를 임베드로 (빈 자리 / 고객 / 농장 / TL). 메시지 총 6000자 제한 안에서"""
    day = [x for x in snap.shifts if x.date == date]
    if not day:
        return {"content": f"No shifts on {date}."}
    gaps = [x for x in day if not x.off and not x.player]
    embeds = [{"title": f"📋 {date} — {sum(not x.off for x in day)} shifts · {len(gaps)} without a player",
               "color": 0xE67E22 if gaps else 0x2ECC71,
               "description": ("⚠️ " + ", ".join(f"{x.account} #{x.slot} `{x.time}`" for x in gaps)) if gaps else "✅ Every shift has a player"}]
    for typ, color in (("Client", 0x3498DB), ("Farming", 0x27AE60)):
        accs = sorted({x.account for x in day if x.type == typ}, key=str.lower)
        lines = []
        for a in accs:
            cells = [f"#{x.slot} " + ("OFF" if x.off else f"{x.time} {x.player or '⚠️'}")
                     for x in sorted((x for x in day if x.account == a), key=lambda x: x.slot)]
            lines.append(f"**{a}** " + " · ".join(cells))
        if lines:
            embeds.append({"title": typ, "color": color, "description": "\n".join(lines)})
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
            rows = index.shifts_on(d, acc, snap=snap)
            if rows:
                days.append(f"`{d[5:]}` " + " · ".join(f"#{x.slot} {'OFF' if x.off else x.time + ' ' + (x.player or '⚠️')}"
                                                        for x in rows))
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

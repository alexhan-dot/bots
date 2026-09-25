"""매니저 컨펌 알림 — 일별(오늘 시프트·플레이어, 오전부터) · 주별(다음 주: 지난주 플레이어 유지해 미리 만든 것).

- 앱 안의 백그라운드 스레드가 1분마다 시각 확인 (Cloud Run min-instances 1 이라 항상 켜져 있음)
- 시각은 Settings 탭: Daily Confirm Time / Weekly Confirm Day·Time / Reminder After (hours)
- 같은 알림은 한 번만 (Firestore 기록) → 인스턴스가 둘이어도 중복 없음
- 디스코드 #schedule-confirm 에 카드 + [Confirm] 버튼 → Confirmations 탭에 기록
"""
import asyncio, datetime, logging, threading, time
from services import clock, index, notify, sheets, state
from services.layout import SETTINGS_TAB, CONFIRM_TAB, CONFIRM_COLS, DAYS, SCHEDULE_TAB, SCHEDULE_COLS

log = logging.getLogger("confirm")
COL = "confirmations"                      # Firestore: 상태 (posted / confirmed)
BANDS = [("🌅 Morning (05–13)", 5, 13), ("☀️ Day (13–21)", 13, 21), ("🌙 Night (21–05)", 21, 29)]
_settings = {"at": 0.0, "v": {}}


# ── 설정 ─────────────────────────────────────
def settings() -> dict:
    if time.time() - _settings["at"] > 600:
        try:
            rows = sheets._book().worksheet(SETTINGS_TAB).get("A2:B20")
            _settings["v"] = {str(r[0]).strip().lower(): (str(r[1]).strip() if len(r) > 1 else "") for r in rows if r}
            _settings["at"] = time.time()
        except Exception as e:
            log.warning("settings read failed: %s", e)
    v = _settings["v"]
    hm = lambda k, d: _hm(v.get(k, d)) or _hm(d)
    day = (v.get("weekly confirm day") or "Sat")[:3].title()
    try: remind = float(v.get("reminder after (hours)") or 3)
    except ValueError: remind = 3.0
    return {"daily": hm("daily confirm time", "07:00"), "weekly_day": day if day in DAYS else "Sat",
            "weekly": hm("weekly confirm time", "12:00"), "remind": remind}


def _hm(s) -> datetime.time | None:
    try:
        h, m = str(s).strip().split(":")[:2]
        return datetime.time(int(h), int(m))
    except Exception:
        return None


# ── 상태 ─────────────────────────────────────
def _key(kind, period): return f"{kind}:{period}"


def status(kind: str, period: str) -> dict:
    doc = state.db().collection(COL).document(_key(kind, period)).get()
    return doc.to_dict() if doc.exists else {}


def _set(kind, period, **kw):
    state.db().collection(COL).document(_key(kind, period)).set(kw, merge=True)


# ── 카드 ─────────────────────────────────────
def _visible(x) -> bool:
    return not x.off and (x.type != "Farming" or bool(x.player))


def _start_hour(t: str) -> int:
    try: return int(t.split(":")[0])
    except (ValueError, AttributeError): return 0


def day_card(date: str, snap=None) -> dict:
    snap = snap or index.get(block=True)
    day = sorted((x for x in snap.shifts if x.date == date and _visible(x)),
                 key=lambda x: (_start_hour(x.time) if _start_hour(x.time) >= 5 else _start_hour(x.time) + 24, x.account.lower()))
    gaps = [x for x in day if not x.player]
    wd = DAYS[(datetime.date.fromisoformat(date).weekday() + 1) % 7]
    fields = []
    for name, lo, hi in BANDS:
        rows = [x for x in day if lo <= (_start_hour(x.time) if _start_hour(x.time) >= 5 else _start_hour(x.time) + 24) < hi]
        if not rows: continue
        text = "\n".join(f"`{x.time}` {x.account} #{x.slot} · {x.player or '⚠️ **no player**'}" for x in rows)
        fields.append({"name": f"{name} — {len(rows)}", "value": text[:1020] + ("…" if len(text) > 1020 else ""), "inline": False})
    desc = (f"⚠️ **{len(gaps)} without a player:** " + ", ".join(f"{x.account} #{x.slot} `{x.time}`" for x in gaps)
            if gaps else "✅ Every shift has a player") + "\nCheck the morning shifts first, then confirm."
    return {"title": f"📅 Daily check — {date} ({wd}) · {len(day)} shifts", "color": 0xE67E22 if gaps else 0x2ECC71,
            "description": desc[:4000], "fields": fields[:25]}


def week_card(sunday: str, snap=None) -> dict:
    snap = snap or index.get(block=True)
    start = datetime.date.fromisoformat(sunday)
    fields, all_gaps, players = [], [], set()
    for i in range(7):
        d = (start + datetime.timedelta(days=i)).isoformat()
        day = [x for x in snap.shifts if x.date == d and _visible(x)]
        gaps = [x for x in day if not x.player]
        all_gaps += [(d, x) for x in gaps]
        players |= {x.player for x in day if x.player}
        fields.append({"name": f"{DAYS[i]} {d[5:]}", "value": f"{len(day)} shifts" + (f" · ⚠️ {len(gaps)} open" if gaps else " · ✅"),
                       "inline": True})
    gap_txt = "\n".join(f"`{d[5:]}` {x.account} #{x.slot} `{x.time}`" for d, x in all_gaps[:25])
    desc = ("Created from last week — **client players kept**, times & hunting grounds copied.\n"
            f"{len(players)} players · {len(all_gaps)} open shifts")
    if gap_txt:
        desc += "\n\n**Open shifts**\n" + gap_txt + ("\n…" if len(all_gaps) > 25 else "")
    desc += "\n\nChange anything with /assign /off /extend or /plan, then confirm."
    return {"title": f"🗓️ Weekly check — week of {sunday}", "color": 0xE67E22 if all_gaps else 0x2ECC71,
            "description": desc[:4000], "fields": fields}


def card_payload(kind: str, period: str, embed: dict, done_by: str = "") -> dict:
    if done_by:
        embed = {**embed, "color": 0x95A5A6, "footer": {"text": f"✅ Confirmed by {done_by} · {clock.stamp()[5:16].replace('T', ' ')}"}}
        rows = []
    else:
        label = "Confirm day" if kind == "day" else "Confirm week"
        rows = [{"type": 1, "components": [
            {"type": 2, "style": 3, "label": label, "emoji": {"name": "✅"}, "custom_id": f"conf_{kind}|{period}"},
            {"type": 2, "style": 2, "label": "Refresh", "emoji": {"name": "🔄"}, "custom_id": f"conf_refresh_{kind}|{period}"}]}]
    return {"embeds": [embed], "components": rows, "allowed_mentions": {"parse": [], "roles": [notify.URGENT_ROLE] if notify.URGENT_ROLE else []}}


def _channel() -> str:
    return notify.CONFIRM_CH or notify.REQUESTS_CH


async def post(kind: str, period: str, remind: bool = False, now: datetime.datetime | None = None) -> bool:
    """카드를 올림. remind=True 면 '아직 컨펌 안 됨' 알림"""
    if not (notify.BOT_TOKEN and _channel()):
        return False
    snap = index.load()                                   # 알림은 최신 시트 기준
    embed = day_card(period, snap) if kind == "day" else week_card(period, snap)
    payload = card_payload(kind, period, embed)
    mention = f"<@&{notify.URGENT_ROLE}> " if notify.URGENT_ROLE else ""
    payload["content"] = (mention + ("⏰ Still not confirmed — " if remind else "") +
                          ("please check today's schedule" if kind == "day" else "please check next week's schedule"))
    msg = await notify.post(_channel(), payload)
    stamp = (now or clock.now()).strftime("%Y-%m-%dT%H:%M")
    if remind:
        _set(kind, period, reminded_at=stamp)
    else:
        _set(kind, period, status="posted", posted_at=stamp, message_id=msg.get("id", ""))
    if not remind:
        _log_sheet(kind, period, "Pending", summary=embed["title"])
    return True


def confirm(kind: str, period: str, by: str):
    _set(kind, period, status="confirmed", by=by, at=clock.stamp())
    _log_sheet(kind, period, "Confirmed", by=by)
    if kind == "week":                                   # 복사 표시(Note) 지우기
        try:
            ws = sheets._book().worksheet(SCHEDULE_TAB)
            vals = ws.get(f"A2:N")
            start = datetime.date.fromisoformat(period)
            days = {(start + datetime.timedelta(days=i)).isoformat() for i in range(7)}
            ncol = chr(65 + SCHEDULE_COLS.index("Note"))
            upd = [{"range": f"{ncol}{i + 2}", "values": [[""]]} for i, v in enumerate(vals)
                   if len(v) >= 14 and sheets._norm_date(v[0]) in days and v[13] == "copied — confirm"]
            if upd:
                ws.batch_update(upd)
        except Exception as e:
            log.warning("note clear failed: %s", e)


def _log_sheet(kind, period, status_, by="", summary=""):
    try:
        book = sheets._book()
        try:
            ws = book.worksheet(CONFIRM_TAB)
        except Exception:
            ws = book.add_worksheet(CONFIRM_TAB, rows=1000, cols=len(CONFIRM_COLS))
            ws.update(values=[CONFIRM_COLS], range_name="A1")
        label = "Day" if kind == "day" else "Week"
        if status_ == "Confirmed":
            cell = next((i + 2 for i, r in enumerate(ws.get("A2:B")) if len(r) >= 2 and r[0] == label and r[1] == period), None)
            if cell:
                ws.batch_update([{"range": f"C{cell}", "values": [["Confirmed"]]},
                                 {"range": f"E{cell}:F{cell}", "values": [[by, clock.stamp()]]}])
                return
        ws.append_row([label, period, status_, clock.stamp() if status_ == "Pending" else "", by,
                       clock.stamp() if status_ == "Confirmed" else "", summary], table_range="A1")
    except Exception as e:
        log.warning("confirmations tab write failed: %s", e)


# ── 스케줄러 ──────────────────────────────────
def _once(key: str) -> bool:
    return state.mark_seen(f"confirm:{key}")


def tick(now: datetime.datetime | None = None):
    """1분마다 호출. 필요한 알림을 올림"""
    now = now or clock.now()
    cfg = settings()
    today = now.date().isoformat()
    t = now.time()
    # 일별: 설정 시각 이후 (23시 전까지) 한 번 + 미컨펌이면 N시간 뒤 한 번 더
    if cfg["daily"] and cfg["daily"] <= t < datetime.time(23, 0):
        if _once(f"day:{today}"):
            asyncio.run(post("day", today, now=now))
        else:
            st = status("day", today)
            due = datetime.datetime.combine(now.date(), cfg["daily"]) + datetime.timedelta(hours=cfg["remind"])
            if st.get("status") != "confirmed" and now.replace(tzinfo=None) >= due and _once(f"day-remind:{today}"):
                asyncio.run(post("day", today, remind=True, now=now))
    # 주별: 설정 요일·시각에 다음 주를 만들고(플레이어 유지) 카드 한 번 + 다음 날 같은 시각까지 미컨펌이면 알림
    wd = DAYS[(now.weekday() + 1) % 7]
    nxt = (sheets.week_start(now.date()) + datetime.timedelta(days=7)).isoformat()
    if wd == cfg["weekly_day"] and cfg["weekly"] and t >= cfg["weekly"] and _once(f"week:{nxt}"):
        try:
            sheets.rollover(datetime.date.fromisoformat(nxt))
            index.invalidate()
        except Exception as e:
            log.warning("weekly rollover failed: %s", e)
        asyncio.run(post("week", nxt, now=now))
    posted = status("week", nxt) if cfg["weekly"] else {}
    if posted.get("status") == "posted" and posted.get("posted_at"):
        due = datetime.datetime.fromisoformat(posted["posted_at"]) + datetime.timedelta(days=1)
        if now.replace(tzinfo=None) >= due and _once(f"week-remind:{nxt}"):
            asyncio.run(post("week", nxt, remind=True, now=now))


def _loop():
    while True:
        try:
            tick()
        except Exception as e:
            log.warning("confirm tick failed: %s", e)
        time.sleep(60)


_started = False


def start():
    global _started
    if _started or not (notify.BOT_TOKEN and _channel()):
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="confirm-scheduler").start()

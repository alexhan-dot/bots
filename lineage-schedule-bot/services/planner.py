"""기간 스케줄 (Planner 탭 / 디스코드 /plan) — 한 달치 등 반복 스케줄을 한 번에 Schedule 에 반영.

Planner 탭 1행 = 반복 규칙:  Account · Slot · Days · Time · Player · Hunting Ground · From · To · Status
  - Days: Daily / Weekdays / Weekends / Mon-Fri / Sat-Mon / Mon,Wed,Fri
  - Time: 09:00-17:00, 9am-5pm, OFF.  Player/Ground/Time 빈 칸 = 기존 값 유지
  - 적용된 규칙은 Status 에 "Applied …" 가 찍히고, 다음 /plan 때는 건너뜀 (reapply 로 다시 적용 가능)
정기점검(Settings 탭)과 겹치는 시간은 Schedule 의 Maint Hrs 열이 계산해 Hours 에서 자동으로 뺌 — 여기서는 미리보기에 경고만.
"""
import datetime, re
from dataclasses import dataclass, field, asdict
from services import sheets, clock
from services.layout import PLANNER_TAB, PLANNER_COLS, SETTINGS_TAB, SCHEDULE_COLS

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]          # date.weekday() 순서
MAX_DAYS = 62                                                         # 한 번에 최대 약 두 달


# ── 입력 해석 ────────────────────────────────
def parse_days(raw: str) -> set[int] | None:
    """"Mon-Fri" → {0..4}. 못 읽으면 None"""
    s = (raw or "").strip().lower()
    if s in ("", "daily", "every day", "everyday", "all", "매일"):
        return set(range(7))
    if s in ("weekdays", "weekday", "평일"):
        return set(range(5))
    if s in ("weekends", "weekend", "주말"):
        return {5, 6}
    out = set()
    for part in re.split(r"[,/\s]+", s):
        if not part:
            continue
        m = re.fullmatch(r"([a-z]{3})[a-z]*\s*(?:-|~|to)\s*([a-z]{3})[a-z]*", part)
        if m:
            if m[1] not in WEEKDAYS or m[2] not in WEEKDAYS:
                return None
            a, b = WEEKDAYS.index(m[1]), WEEKDAYS.index(m[2])
            out |= {(a + i) % 7 for i in range((b - a) % 7 + 1)}
        elif part[:3] in WEEKDAYS:
            out.add(WEEKDAYS.index(part[:3]))
        else:
            return None
    return out or None


def _clock(h: str, m: str | None, ap: str | None) -> int | None:
    h, m = int(h), int(m or 0)
    if ap:
        if not 1 <= h <= 12: return None
        h = h % 12 + (12 if ap.lower() == "pm" else 0)
    if h > 24 or m > 59: return None
    return h * 60 + m


def parse_time(raw) -> str | None:
    """"09:00-17:00" / "9-17" / "9am-5pm" / "12am-8am" → "HH:MM-HH:MM", "OFF" → "OFF". 못 읽으면 None.
    12am 으로 시작 = 24:00 (그날 밤 자정 시작, 기존 시트 표기와 같음)"""
    s = str(raw or "").strip()
    if s.upper() == "OFF":
        return "OFF"
    m = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?\s*([ap]m)?\s*[-~]\s*(\d{1,2})(?::?(\d{2}))?\s*([ap]m)?", s, re.I)
    if not m:
        return None
    a, b = _clock(m[1], m[2], m[3]), _clock(m[4], m[5], m[6])
    if a is None or b is None or a == b:
        return None
    if m[3] and a == 0:
        a = 24 * 60
    if b == 0:
        b = 24 * 60
    return f"{a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}"


def span(time: str) -> tuple[float, float] | None:
    """"16:00-24:00" → (16, 24), "24:00-08:00" → (24, 32), "22:00-06:00" → (22, 30)"""
    m = re.fullmatch(r"(\d+):(\d+)-(\d+):(\d+)", time or "")
    if not m:
        return None
    s, e = int(m[1]) + int(m[2]) / 60, int(m[3]) + int(m[4]) / 60
    return s, s + (e - s) % 24


def _date(raw) -> datetime.date | None:
    s = sheets._norm_date(raw or "")
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


# ── 정기점검 ────────────────────────────────
@dataclass
class Maint:
    weekday: int | None = None           # 0 = Mon
    start: float = 0
    end: float = 0

    def overlap(self, date: datetime.date, time: str) -> float:
        """date 행의 time 시프트가 점검과 겹치는 시간 (시트 Maint Hrs 수식과 같은 규칙)"""
        sp = span(time)
        if self.weekday is None or not sp:
            return 0
        s, e = sp
        total = 0.0
        for offset in (0, 1):                                            # 그날 / 자정 넘어 다음 날
            if (date.weekday() + offset) % 7 == self.weekday:
                total += max(0.0, min(e, 24 * offset + self.end) - max(s, 24 * offset + self.start))
        return total

    def label(self) -> str:
        if self.weekday is None: return "none"
        f = lambda h: f"{int(h):02d}:{int(round(h % 1 * 60)):02d}"
        return f"{WEEKDAYS[self.weekday].title()} {f(self.start)}-{f(self.end)}"


def load_maint(book) -> Maint:
    try:
        rows = {r[0].strip().lower(): (r[1] if len(r) > 1 else "") for r in book.worksheet(SETTINGS_TAB).get("A2:B10") if r}
    except Exception:
        return Maint()
    day = str(rows.get("maintenance day", "")).strip().lower()[:3]
    t = parse_time(f'{rows.get("maintenance start", "")}-{rows.get("maintenance end", "")}')
    if day not in WEEKDAYS or not t or t == "OFF":
        return Maint()
    s, e = span(t)
    return Maint(WEEKDAYS.index(day), s, e)


# ── 규칙 → 변경 목록 ─────────────────────────
@dataclass
class Rule:
    row: int                    # Planner 행 번호 (명령 입력이면 0)
    account: str
    slot: int
    days: set
    time: str | None            # None = 유지
    player: str | None
    ground: str | None
    lo: datetime.date
    hi: datetime.date


@dataclass
class Plan:
    rules: list = field(default_factory=list)
    updates: int = 0
    new: int = 0
    unchanged: int = 0
    maint_shifts: int = 0
    maint_hours: float = 0
    errors: list = field(default_factory=list)        # 규칙을 못 읽음 (해당 규칙은 건너뜀)
    warnings: list = field(default_factory=list)      # 적용은 되지만 확인 필요
    lo: str = ""
    hi: str = ""
    maint: str = ""

    @property
    def changes(self) -> int:
        return self.updates + self.new

    def summary(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "rules"}


def _resolve_account(raw: str, master: dict) -> str | None:
    n = re.sub(r"[\s\-()]", "", str(raw).lower())
    for name, r in master.items():
        names = [name, r.get("KoreanName", "")] + str(r.get("Aliases", "")).split("|")
        if any(x and re.sub(r"[\s\-()]", "", x.lower()) == n for x in names):
            return name
    return None


def make_rule(i: int, rec: dict, master: dict, lo: datetime.date | None, hi: datetime.date | None,
              plan: Plan) -> Rule | None:
    """Planner 한 줄(또는 /plan 입력) → Rule. 문제 있으면 plan.errors 에 적고 None"""
    where = f"Planner row {i}" if i else "/plan"
    acc = _resolve_account(rec.get("Account", ""), master)
    if not acc:
        plan.errors.append(f"{where}: unknown account '{rec.get('Account', '')}'")
        return None
    try:
        slot = int(rec.get("Slot") or 1)
    except (TypeError, ValueError):
        plan.errors.append(f"{where}: slot must be a number")
        return None
    days = parse_days(str(rec.get("Days", "")))
    if days is None:
        plan.errors.append(f"{where}: can't read days '{rec.get('Days')}' (use Daily, Mon-Fri, Mon,Wed,Fri)")
        return None
    time = None
    if str(rec.get("Time", "")).strip():
        time = parse_time(rec["Time"])
        if not time:
            plan.errors.append(f"{where}: can't read time '{rec['Time']}' (use 09:00-17:00, 9am-5pm or OFF)")
            return None
    player = str(rec.get("Player", "")).strip() or None
    ground = str(rec.get("Hunting Ground", "")).strip() or None
    if not (time or player or ground):
        plan.errors.append(f"{where}: nothing to change (fill Time, Player or Hunting Ground)")
        return None
    r_lo, r_hi = _date(rec.get("From")) or lo, _date(rec.get("To")) or hi
    if lo and r_lo and r_lo < lo: r_lo = lo                      # 명령에서 준 기간으로 제한
    if r_lo and r_lo < clock.today(): r_lo = clock.today()          # 지난 날짜는 Archive — 바꾸지 않음
    if hi and r_hi and r_hi > hi: r_hi = hi
    if not r_lo or not r_hi:
        plan.errors.append(f"{where}: needs From and To dates")
        return None
    if r_hi < r_lo:
        return None                                              # 명령 기간과 안 겹침 → 조용히 건너뜀
    if (r_hi - r_lo).days >= MAX_DAYS:
        plan.errors.append(f"{where}: {r_lo}~{r_hi} is longer than {MAX_DAYS} days — split it")
        return None
    if str(master[acc].get("Status", "")) not in ("", "Active"):
        plan.warnings.append(f"{acc} is {master[acc].get('Status')} in Accounts")
    return Rule(i, acc, slot, days, time, player, ground, r_lo, r_hi)


def read_planner(book, reapply=False) -> list[tuple[int, dict]]:
    vals = book.worksheet(PLANNER_TAB).get(f"A2:{chr(64 + len(PLANNER_COLS))}")
    out = []
    for i, v in enumerate(vals):
        rec = dict(zip(PLANNER_COLS, list(v) + [""] * (len(PLANNER_COLS) - len(v))))
        status = str(rec["Status"]).strip()
        if not str(rec["Account"]).strip() or status.upper().startswith("EXAMPLE"):
            continue
        if status.startswith("Applied") and not reapply:
            continue
        out.append((i + 2, rec))
    return out


def build(inline: dict | None = None, lo: str | None = None, hi: str | None = None,
          account: str | None = None, reapply=False, s: "sheets.Schedule | None" = None, book=None):
    """변경 계획 계산 (시트에 쓰지 않음). 반환 (Plan, [(Rule, date, 기존행 or None, 바꿀 값)], Schedule)"""
    book = book or sheets._book()
    master = {r["Account"]: r for r in sheets.load_master()}
    lo_d, hi_d = _date(lo), _date(hi)
    plan = Plan()
    if lo and not lo_d: plan.errors.append(f"can't read from date '{lo}'")
    if hi and not hi_d: plan.errors.append(f"can't read to date '{hi}'")
    recs = [(0, inline)] if inline else read_planner(book, reapply)
    if not recs and not plan.errors:
        plan.errors.append("Planner tab has no new rows. Fill it in (or use /plan with account + time/player).")
    rules = [r for r in (make_rule(i, rec, master, lo_d, hi_d, plan) for i, rec in recs) if r]
    if account:
        acc = _resolve_account(account, master) or account
        rules = [r for r in rules if r.account == acc]
    plan.rules = rules
    s = s or sheets.Schedule()
    maint = load_maint(book)
    changes, final = [], {}                                   # final: (date, account, slot) → row dict (적용 후)
    for rule in rules:                                        # 뒤 규칙이 앞 규칙을 덮어씀 (행 순서)
        d = rule.lo
        while d <= rule.hi:
            if d.weekday() in rule.days:
                existing = {r["Slot"]: (n, r) for n, r in s.on(rule.account, d)}
                key = (d, rule.account, rule.slot)
                base = final.get(key) or (existing.get(rule.slot) or (None, None))[1]
                vals = {}
                if rule.time: vals["Time"] = rule.time
                if rule.player: vals["Player"] = rule.player
                if rule.ground: vals["Hunting Ground"] = rule.ground
                if base is None and "Time" not in vals:
                    plan.warnings.append(f"{rule.account} #{rule.slot} {d}: no shift there yet — add a Time")
                else:
                    changes.append((rule, d, existing.get(rule.slot), vals))
                    final[key] = {**(base or {}), **vals, "Account": rule.account, "Slot": rule.slot, "Date": d.isoformat()}
            d += datetime.timedelta(days=1)
    seen = set()                                              # 통계: 같은 칸을 여러 규칙이 바꾸면 마지막만 셈
    for rule, d, ex, vals in reversed(changes):
        key = (d, rule.account, rule.slot)
        if key in seen: continue
        seen.add(key)
        if ex is None: plan.new += 1
        elif all(str(ex[1].get(k, "")) == str(v) for k, v in vals.items()): plan.unchanged += 1
        else: plan.updates += 1
        row = final[key]
        h = maint.overlap(d, row.get("Time", ""))
        if h and row.get("Time") != "OFF":
            plan.maint_shifts += 1
            plan.maint_hours += h
    if final:
        plan.lo, plan.hi = min(k[0] for k in final).isoformat(), max(k[0] for k in final).isoformat()
    plan.warnings += _double_bookings(s, final)
    plan.warnings = list(dict.fromkeys(plan.warnings))
    plan.maint = maint.label()
    return plan, changes, s


def _double_bookings(s, final: dict) -> list[str]:
    """적용 후 같은 플레이어가 겹치는 시간에 두 시프트 이상인지 (적용 기간 ±1일만 검사)"""
    if not final:
        return []
    days = {k[0] for k in final}
    lo, hi = min(days) - datetime.timedelta(days=1), max(days) + datetime.timedelta(days=1)
    state = {}
    for _, r in s.rows:
        try: d = datetime.date.fromisoformat(r["Date"])
        except ValueError: continue
        if lo <= d <= hi:
            state[(d, r["Account"], r["Slot"])] = r
    for k, r in final.items():
        state[k] = {**state.get(k, {}), **r}
    by_player = {}
    for (d, acc, slot), r in state.items():
        p, sp = str(r.get("Player", "")).strip(), span(r.get("Time", ""))
        if p and sp and r.get("Time") != "OFF":
            base = (d - lo).days * 24
            by_player.setdefault(p.lower(), []).append((base + sp[0], base + sp[1], d, acc, slot, p))
    out = []
    for items in by_player.values():
        items.sort()
        for a, b in zip(items, items[1:]):
            if b[0] < a[1] and ((a[2], a[3], a[4]) in final or (b[2], b[3], b[4]) in final):
                other = f"{b[3]} #{b[4]}" if b[2] == a[2] else f"{b[3]} #{b[4]} ({b[2]})"
                out.append(f"{a[5]} double-booked {a[2]}: {a[3]} #{a[4]} and {other}")
    return out[:10]


# ── 적용 ────────────────────────────────────
def apply(inline=None, lo=None, hi=None, account=None, reapply=False, by: str = "") -> Plan:
    """다시 계산해서 (미리보기 이후 시트가 바뀌었을 수 있음) Schedule 에 반영하고 Planner Status 기록"""
    book = sheets._book()
    sheets.housekeep_if_needed()
    with sheets.LOCK:
        return _apply(book, inline, lo, hi, account, reapply, by)


def _apply(book, inline, lo, hi, account, reapply, by) -> Plan:
    plan, changes, s = build(inline, lo, hi, account, reapply, book=book)
    master = {r["Account"]: r for r in sheets.load_master()}
    added = {}
    for rule, d, ex, vals in changes:
        key = (d, rule.account, rule.slot)
        if ex is not None:
            n, row = ex
            diff = {k: v for k, v in vals.items() if str(row.get(k, "")) != str(v)}
            if diff: s.set(n, row, **diff)
        elif key in added:
            added[key].update(vals)
        else:
            s.add(Date=d.isoformat(), Type=master.get(rule.account, {}).get("Type", ""), Account=rule.account,
                  Slot=rule.slot, **vals)
            added[key] = s._new[-1]
    s.flush()
    stamp = f"Applied {clock.stamp()}" + (f" by {by}" if by else "")
    rows = sorted({r.row for r in plan.rules if r.row})
    if rows:
        ws = book.worksheet(PLANNER_TAB)
        col = chr(65 + PLANNER_COLS.index("Status"))
        per = {}
        for rule, d, *_ in changes:
            per[rule.row] = per.get(rule.row, 0) + 1
        ws.batch_update([{"range": f"{col}{r}", "values": [[f"{stamp} · {per.get(r, 0)} shifts"]]} for r in rows])
    sheets.log_event({"type": "PLAN", "character": ",".join(sorted({r.account for r in plan.rules}))[:100],
                      "summary": f"/plan {plan.lo}~{plan.hi}: {plan.updates} updated, {plan.new} new",
                      "status": "applied", "answer": by})
    return plan

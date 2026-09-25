"""Google Sheets (v2 구조) — Accounts 마스터, Schedule 원장, 작업 반영, EventLog.

  Accounts : 계정 마스터. Type = Client(고객) / Farming(농장), Status = Active/Paused/Inactive
  Schedule : 1행 = 계정 × 날짜 × 시프트(Slot). Time = "HH:MM-HH:MM" | "OFF"
             봇은 A:O만 쓰고, Hours/Week/Key/Display(P:S)는 시트 수식이 계산
  Client Board / Farming Board : Schedule을 주간 그리드로 보여주는 수식 탭 (봇은 건드리지 않음)

주간 탭을 따로 만들지 않고, 새 주 첫 작업 때 직전 주 행(시간·사냥터)을 복사해 이어 붙임.
기존 수기 시트(TargetWeekNN)는 더 이상 읽거나 쓰지 않음.
"""
import os, csv, datetime, functools
import gspread
from google.auth import default
from services import clock
from services.layout import (SCHEDULE_TAB, ACCOUNTS_TAB, SCHEDULE_COLS, METRIC_COLS, ACCOUNT_COLS,
                             DAYS, BOARDS, schedule_formulas, board_formulas,
                             TL_TAB, TL_BOARD, TL_COLS, tl_formulas, tl_board_formulas,
                             PAYROLL_TAB, payroll_formulas, LOG_TABS, week_formula)

SHEET_ID = os.environ["SHEET_ID"]
DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OP_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]   # parser 스케줄 키 (date.weekday() 순서)
COL = {c: i for i, c in enumerate(SCHEDULE_COLS)}

@functools.lru_cache
def _client():
    creds, _ = default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds)

def _book():
    return _client().open_by_key(SHEET_ID)

def _today() -> datetime.date:
    return clock.today()

def week_start(d: datetime.date) -> datetime.date:
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)       # 일요일

def _now() -> str:
    return clock.stamp()

# ── 탭 초기화 ────────────────────────────────
def ensure_tabs():
    """Accounts/Schedule 탭이 없거나 비어 있으면 data/*_seed.csv 로 채움 (시트 첫 연결 시 1회)"""
    book = _book()
    titles = {ws.title for ws in book.worksheets()}
    if ACCOUNTS_TAB not in titles:
        book.add_worksheet(ACCOUNTS_TAB, rows=500, cols=len(ACCOUNT_COLS)).update(
            values=[ACCOUNT_COLS], range_name="A1")
    acc = book.worksheet(ACCOUNTS_TAB)
    if not acc.acell("A2").value:
        with open(os.path.join(DATA, "accounts_seed.csv"), encoding="utf-8") as f:
            rows = list(csv.reader(f))[1:]
        if rows: acc.append_rows(rows, table_range="A1")
    if SCHEDULE_TAB not in titles:
        ws = book.add_worksheet(SCHEDULE_TAB, rows=2000, cols=len(SCHEDULE_COLS) + 4)
        ws.update(values=[SCHEDULE_COLS + [h for h, _ in schedule_formulas().values()]], range_name="A1")
    # 계산 수식은 매 기동 시 열린 범위(A2:A)로 다시 씀 — xlsx 이관본의 제한 범위 해제 + 누가 지워도 복구
    ws = book.worksheet(SCHEDULE_TAB)
    ws.batch_update([{"range": f"{c}2", "values": [[f]]} for c, (_, f) in schedule_formulas().items()],
                    value_input_option="USER_ENTERED")
    for title, type_ in BOARDS.items():
        if title in titles:
            _write_formulas(book.worksheet(title), board_formulas(type_))
    # 매니저 기록 탭(Overtime/Incentives/Death Penalty)은 없으면 헤더만 만들어 둠 (디스코드 봇이 append)
    for title, cols in LOG_TABS.items():
        if title not in titles:
            book.add_worksheet(title, rows=1000, cols=len(cols) + 1).update(
                values=[cols + ["Week"]], range_name="A1")
        _write_formulas(book.worksheet(title), {f"{chr(65 + len(cols))}2": week_formula()})
    if TL_TAB in titles:
        _write_formulas(book.worksheet(TL_TAB), {f"{c}2": f for c, (_, f) in tl_formulas().items()})
    if TL_BOARD in titles:
        _write_formulas(book.worksheet(TL_BOARD), tl_board_formulas())
    if PAYROLL_TAB in titles:
        _write_formulas(book.worksheet(PAYROLL_TAB), payroll_formulas())
    ws = book.worksheet(SCHEDULE_TAB)
    if not ws.acell("A2").value:
        with open(os.path.join(DATA, "schedule_seed.csv"), encoding="utf-8") as f:
            rows = [_typed(r) for r in list(csv.reader(f))[1:]]
        if rows: ws.append_rows(rows, table_range="A1:O1")
    load_master(force=True)

def _write_formulas(ws, cells: dict):
    ws.batch_update([{"range": ref, "values": [[f]]} for ref, f in cells.items()],
                    value_input_option="USER_ENTERED")

def append_log(tab: str, row: dict) -> int:
    """Overtime/Incentives/Death Penalty 탭에 한 줄 추가. 반환: 행 번호(알 수 없으면 0)"""
    cols = LOG_TABS[tab]
    row = {**row, "Logged At": _now()}
    row = {k: ("" if v is None else v) for k, v in row.items()}
    res = _book().worksheet(tab).append_row([row.get(c, "") for c in cols], table_range="A1")
    try:
        return int(res["updates"]["updatedRange"].split("!")[1].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ").split(":")[0])
    except Exception:
        return 0

def update_shift_row(rownum: int, date: str, account: str, slot: int, **fields) -> bool:
    """Schedule 한 행 수정 (디스코드 매니저용). 행이 여전히 같은 시프트인지 확인 후 씀 — 누가 정렬했으면 False"""
    ws = _book().worksheet(SCHEDULE_TAB)
    cur = ws.row_values(rownum)
    cur = cur + [""] * (len(SCHEDULE_COLS) - len(cur))
    try: cur_slot = int(cur[COL["Slot"]])
    except (TypeError, ValueError): cur_slot = None
    if _norm_date(cur[COL["Date"]]) != date or cur[COL["Account"]] != account or cur_slot != slot:
        return False
    data = [{"range": f"{chr(65 + COL[k])}{rownum}", "values": [[v]]} for k, v in fields.items()]
    data.append({"range": f"{chr(65 + COL['Updated'])}{rownum}", "values": [[_now()]]})
    ws.batch_update(data)
    return True

def _typed(r: list) -> list:
    """CSV 문자열 → Slot은 정수, KPI/Gold 숫자면 숫자"""
    r = list(r) + [""] * (len(SCHEDULE_COLS) - len(r))
    r[COL["Slot"]] = int(r[COL["Slot"]])
    for c in ("KPI", "Gold"):
        try: r[COL[c]] = float(r[COL[c]]) if r[COL[c]] else ""
        except ValueError: pass
    return r

# ── Accounts ─────────────────────────────────
_master_cache = {"ts": None, "rows": []}

def load_master(force=False) -> list[dict]:
    now = datetime.datetime.utcnow()
    if not force and _master_cache["ts"] and (now - _master_cache["ts"]).total_seconds() < 300:
        return _master_cache["rows"]
    rows = _book().worksheet(ACCOUNTS_TAB).get_all_records()
    _master_cache.update(ts=now, rows=rows)
    return rows

def master_row(name: str) -> dict | None:
    return next((r for r in load_master() if r["Account"] == name), None)

def active_accounts(type_: str | None = None) -> list[str]:
    return [r["Account"] for r in load_master()
            if r.get("Status") == "Active" and (type_ is None or r.get("Type") == type_)]

def set_status(name: str, status: str):
    ws = _book().worksheet(ACCOUNTS_TAB)
    cell = ws.find(name, in_column=1)
    if cell:
        ws.update_cell(cell.row, ACCOUNT_COLS.index("Status") + 1, status)
    load_master(force=True)

def add_master(row: dict):
    ws = _book().worksheet(ACCOUNTS_TAB)
    headers = ws.row_values(1)
    ws.append_row([row.get(h, "") for h in headers], table_range="A1")
    load_master(force=True)

# ── Schedule 스냅샷 (작업 1건 = 읽기 1번 + 쓰기 몇 번) ──
class Schedule:
    def __init__(self):
        self.ws = _book().worksheet(SCHEDULE_TAB)
        vals = self.ws.get(f"A2:{chr(64 + len(SCHEDULE_COLS))}")
        self.rows = []                      # [(rownum, dict)]
        for i, v in enumerate(vals):
            v = list(v) + [""] * (len(SCHEDULE_COLS) - len(v))
            if not v[0]: continue
            d = dict(zip(SCHEDULE_COLS, v))
            d["Date"] = _norm_date(d["Date"])
            try: d["Slot"] = int(d["Slot"])
            except (TypeError, ValueError): d["Slot"] = 1
            self.rows.append((i + 2, d))
        self._updates, self._new = [], []       # _new: 이번 작업에서 추가할 행(dict, flush 전까지 수정 가능)

    # 조회
    def on(self, account: str, date: datetime.date) -> list[tuple[int, dict]]:
        ds = date.isoformat()
        return sorted(((n, r) for n, r in self.rows if r["Account"] == account and r["Date"] == ds),
                      key=lambda x: x[1]["Slot"])

    def week(self, sunday: datetime.date, account: str | None = None):
        lo, hi = sunday.isoformat(), (sunday + datetime.timedelta(days=6)).isoformat()
        return [(n, r) for n, r in self.rows
                if lo <= r["Date"] <= hi and (account is None or r["Account"] == account)]

    # 쓰기 (flush 때 일괄 반영)
    def set(self, rownum: int | None, row: dict, **fields):
        row.update(fields)
        if rownum is None:                      # 아직 flush 안 된 새 행 → dict만 수정
            return
        for k, v in fields.items():
            self._updates.append({"range": f"{chr(65 + COL[k])}{rownum}", "values": [[v]]})
        self._updates.append({"range": f"{chr(65 + COL['Updated'])}{rownum}", "values": [[_now()]]})

    def add(self, **fields):
        d = datetime.date.fromisoformat(fields["Date"])
        fields.setdefault("Day", DAYS[(d.weekday() + 1) % 7])
        fields.setdefault("Updated", _now())
        row = {c: fields.get(c, "") for c in SCHEDULE_COLS}
        self._new.append(row)
        self.rows.append((None, row))

    def flush(self):
        if self._updates:
            self.ws.batch_update(self._updates)          # RAW — 날짜/시간 문자열 그대로
        if self._new:
            self.ws.append_rows([[r[c] for c in SCHEDULE_COLS] for r in self._new], table_range="A1:O1")
        self._updates, self._new = [], []

    # 새 주 자동 생성
    def ensure_week(self, sunday: datetime.date) -> int:
        """sunday 주에 행이 없으면 가장 최근 주의 Active 계정 행을 복사 (시간·사냥터만). 추가된 행 수 반환"""
        if self.week(sunday):
            return 0
        past = [r["Date"] for _, r in self.rows if r["Date"] < sunday.isoformat()]
        if not past:
            return 0
        src = week_start(datetime.date.fromisoformat(max(past)))
        shift = sunday - src
        accounts = {r["Account"]: r for r in load_master() if r.get("Status") == "Active"}
        n = 0
        for _, r in sorted(self.week(src), key=lambda x: (x[1]["Date"], x[1]["Account"], x[1]["Slot"])):
            if r["Account"] not in accounts:
                continue
            d = datetime.date.fromisoformat(r["Date"]) + shift
            self.add(Date=d.isoformat(), Type=accounts[r["Account"]].get("Type") or r["Type"],
                     Account=r["Account"], Slot=r["Slot"], Time=r["Time"] or "OFF",
                     **{"Hunting Ground": r.get("Hunting Ground", "")})
            n += 1
        return n

    def set_day_times(self, account: str, type_: str, date: datetime.date, times: list[str], n_slots: int):
        """해당 일자의 Slot 1..n_slots 에 시간 배정 (부족분 OFF, 없는 슬롯은 추가)"""
        existing = {r["Slot"]: (n, r) for n, r in self.on(account, date)}
        for slot in range(1, max(n_slots, max(existing, default=0)) + 1):
            t = times[slot - 1] if slot <= len(times) else "OFF"
            if slot in existing:
                n, r = existing[slot]
                if r["Time"] != t: self.set(n, r, Time=t)
            elif slot <= n_slots:
                self.add(Date=date.isoformat(), Type=type_, Account=account, Slot=slot, Time=t)


def _norm_date(v) -> str:
    s = str(v).strip()
    if "/" in s:                                   # 사람이 날짜 서식으로 입력한 경우 (M/D/YYYY)
        try: return datetime.datetime.strptime(s, "%m/%d/%Y").date().isoformat()
        except ValueError: return s
    return s[:10]

def _dates(op, key="dates") -> list[datetime.date]:
    return [datetime.date.fromisoformat(ds) for ds in op.get(key, [])]

# ── 작업 반영 ────────────────────────────────
def apply(op: dict) -> str:
    t = op["type"]
    if t in ("QUESTION", "INFO", "RELOGIN"):
        return log_event(op)
    handler = {"NEW_CHARACTER": _apply_new, "STOP": _apply_stop, "PLAYER_SWAP": _apply_swap,
               "EXTEND": _apply_extend, "HUNTING_GROUND": _apply_hunting_ground,
               "SCHEDULE_LEDGER": _apply_ledger}.get(t)
    if not handler:
        raise ValueError(f"unknown op type {t}")
    s = Schedule()
    result = handler(s, op)
    s.flush()
    return result

def rollover(sunday: datetime.date | None = None) -> int:
    """다음 주(기본) 행 미리 생성 — /week 명령·스케줄러용. Schedule + TL 근무표(근무시간만 복사)"""
    sunday = sunday or week_start(_today()) + datetime.timedelta(days=7)
    s = Schedule()
    n = s.ensure_week(sunday)
    s.flush()
    return n + _rollover_tl(sunday)

def _rollover_tl(sunday: datetime.date) -> int:
    try:
        ws = _book().worksheet(TL_TAB)
    except gspread.WorksheetNotFound:
        return 0
    vals = [r for r in ws.get(f"A2:{chr(64 + len(TL_COLS))}") if r and r[0]]
    lo = sunday.isoformat()
    if any(r[0] >= lo and r[0] <= (sunday + datetime.timedelta(days=6)).isoformat() for r in vals):
        return 0
    past = [r[0] for r in vals if r[0] < lo]
    if not past:
        return 0
    src = week_start(datetime.date.fromisoformat(max(past)))
    shift = sunday - src
    new = []
    for r in vals:
        d = datetime.date.fromisoformat(r[0])
        if src <= d < src + datetime.timedelta(days=7):
            r = list(r) + [""] * (len(TL_COLS) - len(r))
            new.append([(d + shift).isoformat(), r[1], r[2], r[3], "", ""])
    if new:
        ws.append_rows(new, table_range="A1")
    return len(new)

def _apply_new(s: Schedule, op) -> str:
    ch = op.get("character") or op["character_raw"]
    type_ = "Farming" if str(op.get("block", "")).lower().startswith(("farm", "농")) else "Client"
    if not master_row(ch):
        add_master({"Account": ch, "Type": type_, "Class": op.get("class", ""),
                    "Server": op.get("server", ""), "KoreanName": op.get("korean_name", ""),
                    "Customer": op.get("customer", ""), "Status": "Active",
                    "StartDate": _today().isoformat()})
    else:
        set_status(ch, "Active")
        type_ = master_row(ch).get("Type") or type_
    sched = op.get("schedule", {})             # {MON: [{"time":...}] | "OFF"}
    per_day = {d: ([x["time"] for x in v] if isinstance(v, list) else []) for d, v in sched.items()}
    n_slots = max((len(v) for v in per_day.values()), default=1) or 1
    this = week_start(_today()); nxt = this + datetime.timedelta(days=7)
    s.ensure_week(this); s.ensure_week(nxt)
    # 이번 주는 오늘부터, 다음 주는 전체 (이후 주는 자동 생성이 다음 주를 복사)
    days = [this + datetime.timedelta(days=i) for i in range(14)]
    for d in days:
        if d < _today(): continue
        s.set_day_times(ch, type_, d, per_day.get(OP_DAYS[d.weekday()], []), n_slots)
    return f"{ch} [{type_}] 신규 등록 — 슬롯 {n_slots}개, {this}~{nxt + datetime.timedelta(days=6)} 반영"

def _apply_stop(s: Schedule, op) -> str:
    ch = op["character"]; changed = []
    for d in _dates(op):
        s.ensure_week(week_start(d))
        for n, r in s.on(ch, d):
            if r["Time"] != "OFF": s.set(n, r, Time="OFF")
        changed.append(f"{d} OFF")
    # 이번 주 전체가 OFF면 Inactive (행은 기록으로 보존, 다음 주 자동 생성에서 제외)
    week = s.week(week_start(_today()), ch)
    if week and all(r["Time"] == "OFF" for _, r in week):
        set_status(ch, "Inactive")
        changed.append("이번 주 전체 OFF → Inactive")
    return f"{ch}: " + ", ".join(changed) + " (시간 구조 변경 없음)"

def _apply_swap(s: Schedule, op) -> str:
    ch, player = op["character"], op["player"]; changed = []
    for d in _dates(op):
        s.ensure_week(week_start(d))
        for n, r in s.on(ch, d):
            if r["Time"] != "OFF": s.set(n, r, Player=player)
        changed.append(d.isoformat())
    return f"{ch}: {', '.join(changed)} 플레이어 → {player} (시간 구조 변경 없음)"

def _apply_extend(s: Schedule, op) -> str:
    ch = op["character"]; d = datetime.date.fromisoformat(op["date"])
    s.ensure_week(week_start(d))
    for n, r in s.on(ch, d):
        if r["Time"].strip() == op["shift_time"].strip():
            s.set(n, r, Time=op["new_time"])
            return f"{ch} {d}: {op['shift_time']} → {op['new_time']} (해당 일자만 변경)"
    raise ValueError(f"{ch}의 {d} {op['shift_time']} 시프트를 찾지 못했습니다.")

def _apply_hunting_ground(s: Schedule, op) -> str:
    ch = op["character"]
    g = " <-> ".join(op.get("grounds", [])) if op.get("mode") == "alternate" \
        else f"{op.get('ground', '')} {op.get('floor') or ''}".strip()
    scope = op.get("scope", "today"); today = _today()
    s.ensure_week(week_start(today))
    if scope == "permanent":                  # 오늘~이번 주 끝 (+이미 만들어진 다음 주). 이후 주는 복사로 유지
        dates = [today + datetime.timedelta(days=i) for i in range(14)]
    else:
        dates = [today]
    n = 0
    for d in dates:
        for rn, r in s.on(ch, d):
            if r["Time"] == "OFF": continue
            if scope.startswith("shift:") and r["Time"].strip() != scope[6:].strip(): continue
            s.set(rn, r, **{"Hunting Ground": g}); n += 1
    return f"{ch}: 사냥터 {g} ({scope}) — {n}개 시프트 갱신"

def _apply_ledger(s: Schedule, op) -> str:
    """장부형: entries[{date,time,kind,action}] → 추가는 빈(OFF) 슬롯에 배정/새 슬롯, 삭제는 OFF"""
    ch = op["character"]; done = []
    type_ = (master_row(ch) or {}).get("Type") or "Client"
    for e in op.get("entries", []):
        d = datetime.date.fromisoformat(e["date"])
        if d < _today() - datetime.timedelta(days=1):
            continue                                   # 과거 항목 무시
        s.ensure_week(week_start(d))
        rows = s.on(ch, d)
        same = [(n, r) for n, r in rows if r["Time"].strip() == e["time"]]
        if e["action"] == "delete":
            for n, r in same: s.set(n, r, Time="OFF")
        elif not same:
            free = [(n, r) for n, r in rows if r["Time"] == "OFF" and n is not None]
            if free:
                s.set(free[0][0], free[0][1], Time=e["time"])
            else:                                      # 새 슬롯: 해당 주 나머지 요일은 OFF로 채워 그리드 유지
                slot = max((r["Slot"] for _, r in s.week(week_start(d), ch)), default=0) + 1
                for i in range(7):
                    day = week_start(d) + datetime.timedelta(days=i)
                    s.add(Date=day.isoformat(), Type=type_, Account=ch, Slot=slot,
                          Time=e["time"] if day == d else "OFF")
        done.append(f"{e['date']} {e['time']} {'삭제' if e['action'] == 'delete' else '추가'}")
    return f"{ch}: " + (", ".join(done) or "반영할 항목 없음 (지난 날짜)")

def log_event(op) -> str:
    """QUESTION / INFO / RELOGIN 을 EventLog 탭에 기록"""
    book = _book()
    try:
        ws = book.worksheet("EventLog")
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet("EventLog", rows=1000, cols=6)
        ws.update(values=[["Timestamp", "Type", "Character", "Summary", "Status", "Answer"]], range_name="A1")
    ws.append_row([_now(), op["type"],
                   op.get("character") or op.get("character_raw") or "",
                   op.get("question") or op.get("summary") or op.get("reason") or "",
                   "open" if op["type"] == "QUESTION" else "logged", ""], table_range="A1")
    return f"EventLog 기록 ({op['type']})"

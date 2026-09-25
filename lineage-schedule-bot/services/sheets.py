"""Google Sheets (v2 구조) — Accounts 마스터, Schedule 원장, 작업 반영, EventLog.

  Accounts : 계정 마스터. Type = Client(고객) / Farming(농장), Status = Active/Paused/Inactive
  Schedule : 1행 = 계정 × 날짜 × 시프트(Slot). Time = "HH:MM-HH:MM" | "OFF"
             봇은 A:O만 쓰고, Hours/Week/Key/Display(P:S)는 시트 수식이 계산
  Client Board / Farming Board : Schedule을 주간 그리드로 보여주는 수식 탭 (봇은 건드리지 않음)

주간 탭을 따로 만들지 않고, 새 주 첫 작업 때 직전 주 행(시간·사냥터)을 복사해 이어 붙임.
기존 수기 시트(TargetWeekNN)는 더 이상 읽거나 쓰지 않음.
"""
import os, re, csv, datetime, functools, threading, logging
import gspread
from google.auth import default
from services import clock, shiftutil
from services.layout import (SCHEDULE_TAB, ARCHIVE_TAB, ALL_TAB, TODAY_TAB, TODAY_COLS, all_shifts_formula,
                             today_formulas, WEEK_START_FORMULA, ACCOUNTS_TAB, SCHEDULE_COLS, METRIC_COLS, ACCOUNT_COLS,
                             DAYS, BOARDS, schedule_formulas, board_formulas,
                             TL_TAB, TL_BOARD, TL_COLS, tl_formulas, tl_board_formulas, pay_week_formula,
                             SETTINGS_TAB, SETTINGS_ROWS, PLANNER_TAB, PLANNER_COLS, PLANNER_EXAMPLE,
                             PAY_ANCHOR, PAYROLL_NOTE,
                             PAYROLL_TAB, payroll_formulas, LOG_TABS, week_formula, SPILL_SEEDS)

SHEET_ID = os.environ["SHEET_ID"]
DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OP_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]   # parser 스케줄 키 (date.weekday() 순서)
COL = {c: i for i, c in enumerate(SCHEDULE_COLS)}

log = logging.getLogger("sheets")
LOCK = threading.RLock()        # Schedule 읽기→쓰기 사이에 정리(보관·정렬)가 끼어들지 않도록


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
    _ensure_settings(book, titles)
    ws = book.worksheet(SCHEDULE_TAB)
    _write_calc_cols(ws, schedule_formulas())
    _ensure_views(book, titles)
    for title, type_ in BOARDS.items():
        if title in titles:
            _write_formulas(book.worksheet(title), {"B2": WEEK_START_FORMULA, **board_formulas(type_)})
    # 매니저 기록 탭(Overtime/Incentives/Death Penalty)은 없으면 헤더만 만들어 둠 (디스코드 봇이 append)
    for title, cols in LOG_TABS.items():
        if title not in titles:
            book.add_worksheet(title, rows=1000, cols=len(cols) + 1).update(
                values=[cols + ["Week"]], range_name="A1")
        wk, pw = chr(65 + len(cols)), chr(66 + len(cols))
        _write_calc_cols(book.worksheet(title), {wk: ("Week", week_formula()), pw: ("Pay Week", pay_week_formula())})
    if TL_TAB in titles:
        _write_calc_cols(book.worksheet(TL_TAB), tl_formulas())
    if TL_BOARD in titles:
        _write_formulas(book.worksheet(TL_BOARD), {"B2": WEEK_START_FORMULA, **tl_board_formulas()})
    if PAYROLL_TAB in titles:
        _ensure_payroll(book.worksheet(PAYROLL_TAB))
    ws = book.worksheet(SCHEDULE_TAB)
    if not ws.acell("A2").value:
        with open(os.path.join(DATA, "schedule_seed.csv"), encoding="utf-8") as f:
            rows = [_typed(r) for r in list(csv.reader(f))[1:]]
        if rows: ws.append_rows(rows, table_range="A1:O1")
    load_master(force=True)

def _write_calc_cols(ws, cols: dict):
    """계산 열: 1행 헤더 + 2행 ARRAYFORMULA. 열이 모자라면 늘림"""
    need = max(ord(c) - 64 for c in cols)
    if ws.col_count < need:
        ws.add_cols(need - ws.col_count)
    data = []
    for c, (head, f) in cols.items():
        data += [{"range": f"{c}1", "values": [[head]]}, {"range": f"{c}2", "values": [[f]]}]
    ws.batch_update(data, value_input_option="USER_ENTERED")

def _ensure_settings(book, titles):
    """Settings 탭(정기점검 시간)과 Planner 탭이 없으면 만듦 — 값이 이미 있으면 건드리지 않음"""
    if SETTINGS_TAB not in titles:
        book.add_worksheet(SETTINGS_TAB, rows=20, cols=3).update(values=SETTINGS_ROWS, range_name="A1")
    else:                                               # 새로 생긴 설정 줄만 뒤에 추가 (기존 값 유지)
        ws = book.worksheet(SETTINGS_TAB)
        have = {str(r[0]).strip() for r in ws.get("A1:A30") if r}
        new = [r for r in SETTINGS_ROWS[1:] if r[0] not in have]
        if new:
            ws.append_rows(new, table_range="A1")
    if PLANNER_TAB not in titles:
        book.add_worksheet(PLANNER_TAB, rows=500, cols=len(PLANNER_COLS)).update(
            values=[PLANNER_COLS] + PLANNER_EXAMPLE, range_name="A1")

def _ensure_views(book, titles):
    """Schedule Archive(지난 시프트) · All Shifts(숨김 합본) · Today(맨 앞) 탭"""
    calc = schedule_formulas()
    head = SCHEDULE_COLS + [h for h, _ in calc.values()]
    if ARCHIVE_TAB not in titles:
        book.add_worksheet(ARCHIVE_TAB, rows=2000, cols=len(head)).update(values=[head], range_name="A1")
    _write_calc_cols(book.worksheet(ARCHIVE_TAB), calc)
    if ALL_TAB not in titles:
        ws = book.add_worksheet(ALL_TAB, rows=5000, cols=len(head))
        ws.update(values=[head], range_name="A1")
        try: ws.hide()
        except Exception: pass
    ws = book.worksheet(ALL_TAB)
    ws.update(values=[[all_shifts_formula()]], range_name="A2", value_input_option="USER_ENTERED")
    if TODAY_TAB not in titles:
        book.add_worksheet(TODAY_TAB, rows=300, cols=20)
    t = book.worksheet(TODAY_TAB)
    t.update(values=[TODAY_COLS + [""] + TODAY_COLS], range_name="A3")
    t.batch_update([{"range": k, "values": [[v]]} for k, v in today_formulas().items()],
                   value_input_option="USER_ENTERED")
    try:
        t.update_index(0)                                   # 맨 앞 탭
        t.format("A1:T1", {"textFormat": {"bold": True, "fontSize": 13}})
        t.format("A3:T3", {"textFormat": {"bold": True}, "backgroundColor": {"red": .85, "green": .9, "blue": 1}})
        book.update_timezone(clock.TZ_NAME)                # 시트의 TODAY() 를 봇과 같은 시간대로
    except Exception as e:
        log.warning("today tab styling skipped: %s", e)
    _grow_all(book)


def _grow_all(book):
    """All Shifts 는 FILTER 결과가 펼쳐질 행이 있어야 함 → Schedule+Archive 행 수 + 여유"""
    try:
        need = sum(len(book.worksheet(t).col_values(1)) for t in (SCHEDULE_TAB, ARCHIVE_TAB)) + 1000
        ws = book.worksheet(ALL_TAB)
        if ws.row_count < need:
            ws.add_rows(need - ws.row_count)
    except Exception as e:
        log.warning("All Shifts resize skipped: %s", e)


# ── 매일 정리: 지난 시프트 → Schedule Archive, 남은 것은 날짜순 (오늘이 맨 위) ──
_last_housekeep = None


def _serial_to_iso(v, with_time=False):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        dt = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=float(v))
        return dt.strftime("%Y-%m-%dT%H:%M") if with_time else dt.date().isoformat()
    return v


def housekeep(force=False) -> int:
    """반환: 보관한 행 수. 하루 한 번만 실제로 실행 (force 로 강제)"""
    global _last_housekeep
    today = _today()
    if not force and _last_housekeep == today:
        return 0
    with LOCK:
        book = _book()
        ws = book.worksheet(SCHEDULE_TAB)
        last = len(ws.col_values(1))
        width = len(SCHEDULE_COLS)
        end = chr(64 + width)
        vals = ws.get(f"A2:{end}{max(last, 2)}", value_render_option="UNFORMATTED_VALUE") if last >= 2 else []
        rows = []
        for v in vals:
            v = list(v) + [""] * (width - len(v))
            if v[0] in ("", None): continue
            v[0] = _norm_date(_serial_to_iso(v[0]))
            v[COL["Updated"]] = _serial_to_iso(v[COL["Updated"]], with_time=True)
            rows.append(v[:width])
        iso = today.isoformat()
        past = [r for r in rows if str(r[0]) < iso]
        keep = [r for r in rows if str(r[0]) >= iso]
        key = lambda r: (str(r[0]), r[COL["Type"]] != "Client", str(r[COL["Account"]]).lower(), str(r[COL["Slot"]]))
        sorted_keep = sorted(keep, key=key)
        if past:
            book.worksheet(ARCHIVE_TAB).append_rows(sorted(past, key=key), value_input_option="RAW",
                                                    table_range=f"A1:{end}1")
        if past or sorted_keep != keep:
            if sorted_keep:
                ws.update(values=sorted_keep, range_name=f"A2:{end}{len(sorted_keep) + 1}", value_input_option="RAW")
            if last > len(sorted_keep) + 1:
                ws.batch_clear([f"A{len(sorted_keep) + 2}:{end}{last}"])
            log.info("housekeep: archived %d, kept %d", len(past), len(sorted_keep))
        _grow_all(book)
        _last_housekeep = today
        return len(past)


def sort_schedule(ws=None):
    """Schedule A:O 를 날짜 → 고객/농장 → 계정 → 슬롯 순으로 (시트 서버에서 정렬, 계산 열 P:U 는 따라 계산됨)"""
    ws = ws or _book().worksheet(SCHEDULE_TAB)
    last = len(ws.col_values(1))
    if last > 2:
        ws.sort((1, "asc"), (3, "asc"), (4, "asc"), (5, "asc"), range=f"A2:{chr(64 + len(SCHEDULE_COLS))}{last}")


def housekeep_if_needed():
    if _last_housekeep != _today():
        try:
            housekeep()
            from services import index
            index.invalidate()
        except Exception as e:
            log.warning("housekeep failed: %s", e)


def _ensure_payroll(ws):
    """Payroll 수식. B2(보고 있는 기간)는 사람이 날짜를 넣었으면 유지, G2(기준일)는 월요일이어야 함"""
    f = payroll_formulas()
    anchor = _norm_date(ws.acell("G2").value or "")
    try: monday = datetime.date.fromisoformat(anchor).weekday() == 0
    except ValueError: monday = False
    extra = {} if monday else {"G2": PAY_ANCHOR}
    b2 = ws.acell("B2", value_render_option="FORMULA").value or ""
    if b2 and not str(b2).startswith("="):          # 사람이 입력한 기간 시작일 → 유지
        f.pop("B2")
    f.update(extra)
    f["H2"] = PAYROLL_NOTE
    _write_formulas(ws, f)

def _write_formulas(ws, cells: dict):
    if ws.title in SPILL_SEEDS:                       # xlsx 이관본의 값 목록을 지워야 A5 수식이 펼쳐짐
        ws.batch_clear([SPILL_SEEDS[ws.title]])
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

def update_shift_row(rownum: int, date: str, account: str, slot: int, tab: str = SCHEDULE_TAB, **fields) -> bool:
    """Schedule(또는 Archive) 한 행 수정. 행이 옮겨졌으면(정렬·보관) 날짜·계정·슬롯으로 다시 찾음. 없으면 False"""
    with LOCK:
        ws = _book().worksheet(tab)
        ok = False
        if rownum and rownum >= 2:
            cur = ws.row_values(rownum)
            cur = cur + [""] * (len(SCHEDULE_COLS) - len(cur))
            try: cur_slot = int(cur[COL["Slot"]])
            except (TypeError, ValueError): cur_slot = None
            ok = _norm_date(cur[COL["Date"]]) == date and cur[COL["Account"]] == account and cur_slot == slot
        if not ok:
            rownum = None
            for i, v in enumerate(ws.get(f"A2:E")):
                v = list(v) + [""] * (5 - len(v))
                try: sl = int(v[4])
                except (TypeError, ValueError): continue
                if _norm_date(v[0]) == date and v[3] == account and sl == slot:
                    rownum = i + 2; break
            if not rownum:
                return False
        data = [{"range": f"{chr(65 + COL[k])}{rownum}", "values": [[v]]} for k, v in fields.items()]
        data.append({"range": f"{chr(65 + COL['Updated'])}{rownum}", "values": [[_now()]]})
        ws.batch_update(data)
        return True


def update_shift_anywhere(date: str, account: str, slot: int, **fields) -> bool:
    """Schedule 에 없으면(이미 지난 날짜) Archive 에서 찾아 수정 — 스크린샷 기록(KPI·Gold)용"""
    return (update_shift_row(0, date, account, slot, **fields)
            or update_shift_row(0, date, account, slot, tab=ARCHIVE_TAB, **fields))

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
ARCHIVED, ARCHIVE_TAIL = -1, 1500


class Schedule:
    def __init__(self):
        book = _book()
        self.ws = book.worksheet(SCHEDULE_TAB)
        end = chr(64 + len(SCHEDULE_COLS))
        self.rows = []                      # [(rownum, dict)] — Archive 행은 rownum=ARCHIVED (읽기 전용)
        self._load(self.ws.get(f"A2:{end}"), 2)
        try:                                # 지난 2~3주 (다음 주 복사·어제 기록용). 수정은 하지 않음
            aw = book.worksheet(ARCHIVE_TAB)
            n = len(aw.col_values(1))
            if n >= 2:
                start = max(2, n - ARCHIVE_TAIL + 1)
                self._load(aw.get(f"A{start}:{end}{n}"), None)
        except gspread.WorksheetNotFound:
            pass
        self._updates, self._new = [], []       # _new: 이번 작업에서 추가할 행(dict, flush 전까지 수정 가능)

    def _load(self, vals, first_row):
        for i, v in enumerate(vals):
            v = list(v) + [""] * (len(SCHEDULE_COLS) - len(v))
            if not v[0]: continue
            d = dict(zip(SCHEDULE_COLS, v))
            d["Date"] = _norm_date(d["Date"])
            try: d["Slot"] = int(d["Slot"])
            except (TypeError, ValueError): d["Slot"] = 1
            self.rows.append((first_row + i if first_row else ARCHIVED, d))

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
        if rownum == ARCHIVED:                  # 지난 시프트는 Archive 탭 — 봇이 고치지 않음
            return
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
            sort_schedule(self.ws)                  # 새 행은 맨 아래에 붙으므로 날짜순으로 다시 (오늘이 맨 위)
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
        accounts = {r["Account"]: r for r in load_master() if r.get("Status") == "Active"
                    and not ((_until(r) or datetime.date.max) < sunday)}      # 기간 끝난 계정 제외
        # 고객 계정 플레이어: 같은 계정·슬롯·요일에 가장 최근(최대 3주)에 들어갔던 플레이어 (초안 주처럼 비어 있어도 유지)
        recent = {}
        lo = (sunday - datetime.timedelta(days=21)).isoformat()
        for _, r in sorted(self.rows, key=lambda x: x[1]["Date"]):
            if lo <= r["Date"] < sunday.isoformat() and str(r.get("Player", "")).strip() and r["Time"] != "OFF":
                wd = datetime.date.fromisoformat(r["Date"]).weekday()
                recent[(r["Account"], r["Slot"], wd, r["Time"])] = r["Player"]
                recent[(r["Account"], r["Slot"], wd)] = r["Player"]
        n = 0
        for _, r in sorted(self.week(src), key=lambda x: (x[1]["Date"], x[1]["Account"], x[1]["Slot"])):
            if r["Account"] not in accounts or str(r["Time"]).strip() in ("", "OFF"):
                continue                                    # OFF 는 복사하지 않음 (보드가 깔끔하게)
            d = datetime.date.fromisoformat(r["Date"]) + shift
            type_ = accounts[r["Account"]].get("Type") or r["Type"]
            # 고객 계정은 지난주 플레이어를 그대로 유지 (매니저가 주간 컨펌에서 확인). 농장은 비움
            wd = d.weekday()
            player = (str(r.get("Player", "")).strip() or recent.get((r["Account"], r["Slot"], wd, r["Time"]))
                      or recent.get((r["Account"], r["Slot"], wd), "")) if type_ == "Client" else ""
            self.add(Date=d.isoformat(), Type=type_, Account=r["Account"], Slot=r["Slot"], Time=r["Time"],
                     Player=player, **{"Hunting Ground": r.get("Hunting Ground", "")},
                     Note="copied — confirm")
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
    housekeep_if_needed()
    with LOCK:
        s = Schedule()
        result = handler(s, op)
        s.flush()
    return result

def rollover(sunday: datetime.date | None = None) -> int:
    """다음 주(기본) 행 미리 생성 — /week 명령·스케줄러용. Schedule + TL 근무표(근무시간만 복사)"""
    sunday = sunday or week_start(_today()) + datetime.timedelta(days=7)
    housekeep_if_needed()
    with LOCK:
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
    # 기간: "내일부터 일주일간" → start/end. 없으면 오늘부터 이번 주 + 다음 주 (이후 주는 자동 생성이 복사)
    lo = _parse_day(op.get("start_date")) or _today()
    hi = _parse_day(op.get("end_date")) or (week_start(_today()) + datetime.timedelta(days=13))
    lo = max(lo, _today())
    hi = min(hi, lo + datetime.timedelta(days=61))
    if op.get("end_date"):                      # 기간 한정 → 끝난 뒤 주에는 자동 복사하지 않음
        _set_until(ch, hi)
    ground = op.get("ground") or ""
    day_times: dict = {}                        # 8시간 넘는 시간은 8시간씩 나눠 슬롯을 늘림 (자정 넘는 조각은 다음 날)
    d = lo
    while d <= hi:
        for t in per_day.get(OP_DAYS[d.weekday()], []):
            for dd, tt in shiftutil.split(d, t):
                day_times.setdefault(dd, []).append(tt)
        day_times.setdefault(d, [])
        d += datetime.timedelta(days=1)
    n_slots = max((len(v) for v in day_times.values()), default=1) or 1
    for d in sorted(day_times):
        s.ensure_week(week_start(d))
        s.set_day_times(ch, type_, d, day_times[d], len(day_times[d]) or 1)
        if ground:
            for n, r in s.on(ch, d):
                if r["Time"] != "OFF" and r.get("Hunting Ground") != ground:
                    s.set(n, r, **{"Hunting Ground": ground})
    extra = f", 사냥터 {ground}" if ground else ""
    return f"{ch} [{type_}] 신규 등록 — 슬롯 {n_slots}개, {lo}~{hi} 반영{extra}"


def _parse_day(v) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


UNTIL_RE = re.compile(r"Until:\s*(\d{4}-\d{2}-\d{2})")


def _set_until(name: str, until: datetime.date):
    """Accounts Notes 에 'Until: YYYY-MM-DD' — 그 뒤 주는 ensure_week 가 복사하지 않음"""
    r = master_row(name)
    if not r:
        return
    notes = UNTIL_RE.sub("", str(r.get("Notes", ""))).strip(" ;")
    notes = (notes + "; " if notes else "") + f"Until: {until.isoformat()}"
    ws = _book().worksheet(ACCOUNTS_TAB)
    cell = ws.find(name, in_column=1)
    if cell:
        ws.update_cell(cell.row, ACCOUNT_COLS.index("Notes") + 1, notes)
        load_master(force=True)


def _until(r: dict) -> datetime.date | None:
    m = UNTIL_RE.search(str(r.get("Notes", "")))
    return _parse_day(m[1]) if m else None

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
    type_ = (master_row(ch) or {}).get("Type") or "Client"
    parts = shiftutil.split(d, op["new_time"])                 # 8시간 넘으면 나머지는 추가 슬롯 (다른 플레이어용)
    for n, r in s.on(ch, d):
        if r["Time"].strip() == op["shift_time"].strip():
            s.set(n, r, Time=parts[0][1])
            keep = {k: r.get(k, "") for k in ("Player", "Hunting Ground") if r.get(k)}   # 연장은 보통 같은 플레이어
            extra = [add_shift(s, ch, type_, dd, t, **keep) for dd, t in parts[1:]]
            more = f" + 추가 슬롯 {', '.join(extra)}" if extra else ""
            return f"{ch} {d}: {op['shift_time']} → {parts[0][1]}{more} (해당 일자만 변경)"
    raise ValueError(f"{ch}의 {d} {op['shift_time']} 시프트를 찾지 못했습니다.")


def add_shift(s: "Schedule", ch: str, type_: str, d: datetime.date, time: str, **extra) -> str:
    """그 날짜의 빈(OFF) 슬롯에 넣거나, 없으면 슬롯을 하나 늘림. 반환: "MM-DD #슬롯 시간" """
    s.ensure_week(week_start(d))
    rows = s.on(ch, d)
    free = [(n, r) for n, r in rows if r["Time"] == "OFF" and n != ARCHIVED]
    if free:
        n, r = free[0]
        s.set(n, r, Time=time, **extra)
        slot = r["Slot"]
    else:
        slot = max((r["Slot"] for _, r in rows), default=0) + 1
        s.add(Date=d.isoformat(), Type=type_, Account=ch, Slot=slot, Time=time, **extra)
    return f"{d.isoformat()[5:]} #{slot} {time}"


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
            for dd, t in shiftutil.split(d, e["time"]):          # 나눠 들어간 조각까지 삭제
                for n, r in s.on(ch, dd):
                    if r["Time"].strip() in (t, e["time"]): s.set(n, r, Time="OFF")
        elif not same:
            for dd, t in shiftutil.split(d, e["time"]):
                if not any(r["Time"].strip() == t for _, r in s.on(ch, dd)):
                    add_shift(s, ch, type_, dd, t)
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

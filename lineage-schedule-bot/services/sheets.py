"""Google Sheets — CharacterMaster 로드, 주간 탭 식별/생성, 4가지 작업 반영.

봇이 생성하는 표준 주간 탭 레이아웃 (탭명: W{ISO주차}_{YYYY-MM-DD}):
  A: Character | B: Class | C: Block | D: Shift | E~K: SUN..SAT (셀값 = 시간 or 플레이어명 라인)
  캐릭터당 시프트 수만큼 행. 셀 형식: "08:00-16:00\n{Player}" / "OFF"
기존 수기 탭(병합 셀 구조)은 읽기 전용으로 두고, 봇 가동 주부터 표준 탭 사용.
"""
import os, datetime, functools
import gspread
from google.auth import default

SHEET_ID = os.environ["SHEET_ID"]
MASTER_TAB = "CharacterMaster"
DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]

@functools.lru_cache
def _client():
    creds, _ = default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds)

def _book():
    return _client().open_by_key(SHEET_ID)

# ── CharacterMaster ──────────────────────────
_master_cache = {"ts": None, "rows": []}

def load_master(force=False) -> list[dict]:
    now = datetime.datetime.utcnow()
    if not force and _master_cache["ts"] and (now - _master_cache["ts"]).seconds < 300:
        return _master_cache["rows"]
    ws = _book().worksheet(MASTER_TAB)
    rows = ws.get_all_records()
    _master_cache.update(ts=now, rows=rows)
    return rows

def master_row(canonical: str) -> dict | None:
    return next((r for r in load_master() if r["CanonicalName"] == canonical), None)

def set_status(canonical: str, status: str):
    ws = _book().worksheet(MASTER_TAB)
    cell = ws.find(canonical, in_column=1)
    if cell:
        headers = ws.row_values(1)
        ws.update_cell(cell.row, headers.index("Status") + 1, status)
    load_master(force=True)

def add_master(row: dict):
    ws = _book().worksheet(MASTER_TAB)
    headers = ws.row_values(1)
    ws.append_row([row.get(h, "") for h in headers])
    load_master(force=True)

# ── 주간 탭 ──────────────────────────────────
def week_tab_name(d: datetime.date) -> str:
    sunday = d - datetime.timedelta(days=(d.weekday() + 1) % 7)
    iso = sunday.isocalendar()
    return f"W{iso.week:02d}_{sunday.isoformat()}"

def get_or_create_week(d: datetime.date):
    book = _book()
    name = week_tab_name(d)
    try:
        return book.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(name, rows=200, cols=12)
        sunday = datetime.date.fromisoformat(name.split("_")[1])
        header = ["Character", "Class", "Block", "Shift"] + \
                 [f"{DAYS[i]} {sunday + datetime.timedelta(days=i)}" for i in range(7)]
        ws.update("A1", [header])
        # Active 캐릭터 블록 생성 (직전 주 탭에서 시프트 구조 복사, 없으면 빈 1행)
        prev = None
        try:
            prev = book.worksheet(week_tab_name(sunday - datetime.timedelta(days=7)))
        except gspread.WorksheetNotFound:
            pass
        rows = []
        for m in load_master():
            if m.get("Status") != "Active":
                continue
            shifts = _prev_shifts(prev, m["CanonicalName"]) or ["-"]
            for sh in shifts:
                rows.append([m["CanonicalName"], m.get("Class", ""), m.get("Block", ""),
                             sh] + ["OFF"] * 7)
        if rows:
            ws.update("A2", rows)
        return ws

def _prev_shifts(prev_ws, character):
    if not prev_ws:
        return None
    vals = prev_ws.get_all_values()
    return [r[3] for r in vals[1:] if r and r[0] == character] or None

def _find_rows(ws, character):
    vals = ws.get_all_values()
    return [(i + 1, r) for i, r in enumerate(vals) if r and r[0] == character]

def _day_col(ws, date: datetime.date) -> int:
    sunday = datetime.date.fromisoformat(ws.title.split("_")[1])
    offset = (date - sunday).days
    if not 0 <= offset <= 6:
        raise ValueError(f"{date}는 {ws.title} 범위 밖입니다.")
    return 5 + offset   # E=5

# ── 작업 반영 ────────────────────────────────
def apply(op: dict) -> str:
    t = op["type"]
    if t == "NEW_CHARACTER":
        return _apply_new(op)
    if t == "STOP":
        return _apply_stop(op)
    if t == "PLAYER_SWAP":
        return _apply_swap(op)
    if t == "EXTEND":
        return _apply_extend(op)
    if t == "HUNTING_GROUND":
        return _apply_hunting_ground(op)
    if t == "SCHEDULE_LEDGER":
        return _apply_ledger(op)
    if t in ("QUESTION", "INFO", "RELOGIN"):
        return log_event(op)
    raise ValueError(f"unknown op type {t}")

def _set_cell_line(ws, row, col, line_idx, value):
    """셀 = "시간\n플레이어\n@사냥터" 구조. line_idx 줄만 교체."""
    cur = (ws.cell(row, col).value or "").split("\n")
    while len(cur) <= line_idx: cur.append("")
    cur[line_idx] = value
    ws.update_cell(row, col, "\n".join(x for x in cur if x != "" or cur.index(x) < line_idx))

def _apply_hunting_ground(op) -> str:
    ch = op["character"]
    g = " <-> ".join(op.get("grounds", [])) if op.get("mode") == "alternate" \
        else f"{op.get('ground','')} {op.get('floor') or ''}".strip()
    tag = f"@{g}"
    today = datetime.date.today()
    ws = get_or_create_week(today)
    rows = _find_rows(ws, ch)
    scope = op.get("scope", "today")
    if scope == "permanent":
        cols = range(5, 12)
    else:
        cols = [_day_col(ws, today)]
    n = 0
    for rownum, row in rows:
        if scope.startswith("shift:") and row[3].strip() != scope[6:].strip():
            continue
        for col in cols:
            if (ws.cell(rownum, col).value or "OFF") == "OFF":
                continue
            _set_cell_line(ws, rownum, col, 2, tag); n += 1
    return f"{ch}: 사냥터 {g} ({scope}) — {n}개 시프트 셀 갱신"

def _apply_ledger(op) -> str:
    """장부형: entries[{date,time,kind,action}] → 추가는 셀에 시간 기록, 삭제는 OFF"""
    ch = op["character"]; done = []
    m = master_row(ch) or {}
    for e in op.get("entries", []):
        d = datetime.date.fromisoformat(e["date"])
        if d < datetime.date.today() - datetime.timedelta(days=1):
            continue                                   # 과거 항목 무시
        ws = get_or_create_week(d); col = _day_col(ws, d)
        rows = [(r, v) for r, v in _find_rows(ws, ch) if v[3].strip() == e["time"]]
        if not rows:                                   # 해당 시프트 행이 없으면 생성
            ws.append_row([ch, m.get("Class", ""), m.get("Block", ""), e["time"]] + ["OFF"] * 7)
            rows = [(r, v) for r, v in _find_rows(ws, ch) if v[3].strip() == e["time"]]
        for rownum, _ in rows:
            ws.update_cell(rownum, col, "OFF" if e["action"] == "delete" else e["time"])
        done.append(f"{e['date']} {e['time']} {'삭제' if e['action']=='delete' else '추가'}")
    return f"{ch}: " + ", ".join(done)

def log_event(op) -> str:
    """QUESTION / INFO / RELOGIN 을 EventLog 탭에 기록"""
    book = _book()
    try:
        ws = book.worksheet("EventLog")
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet("EventLog", rows=1000, cols=6)
        ws.update("A1", [["Timestamp", "Type", "Character", "Summary", "Status", "Answer"]])
    ws.append_row([datetime.datetime.now().isoformat(timespec="minutes"), op["type"],
                   op.get("character") or op.get("character_raw") or "",
                   op.get("question") or op.get("summary") or op.get("reason") or "",
                   "open" if op["type"] == "QUESTION" else "logged", ""])
    return f"EventLog 기록 ({op['type']})"

def _apply_new(op) -> str:
    ch = op["character"] or op["character_raw"]
    if not master_row(ch):
        add_master({"CanonicalName": ch, "Class": op.get("class", ""),
                    "Server": op.get("server", ""), "Block": op.get("block", "Client"),
                    "KoreanName": op.get("korean_name", ""), "Aliases": "",
                    "Status": "Active"})
    else:
        set_status(ch, "Active")
    today = datetime.date.today()
    ws = get_or_create_week(today)
    # 기존 행 제거 후 시프트별 행 삽입
    for rownum, _ in reversed(_find_rows(ws, ch)):
        ws.delete_rows(rownum)
    sched = op["schedule"]     # {MON: [{"time":...}] | "OFF"}
    shift_map = {}             # shift_time -> {day: time}
    for day, v in sched.items():
        if v == "OFF" or not v:
            continue
        for s in v:
            shift_map.setdefault(s["time"], {})[day] = s["time"]
    rows = []
    m = master_row(ch) or {}
    for shift, days in shift_map.items():
        rows.append([ch, m.get("Class", op.get("class", "")), m.get("Block", ""), shift] +
                    [days.get(d, "OFF") for d in DAYS])
    ws.append_rows(rows)
    return f"{ws.title}: {ch} 신규 블록 {len(rows)}행 추가"

def _apply_stop(op) -> str:
    ch = op["character"]
    changed = []
    for ds in op["dates"]:
        d = datetime.date.fromisoformat(ds)
        ws = get_or_create_week(d)
        col = _day_col(ws, d)
        for rownum, _ in _find_rows(ws, ch):
            ws.update_cell(rownum, col, "OFF")
        changed.append(f"{ds} OFF")
    # 이번 주 전체가 OFF면 블록 제거 + Inactive
    _cleanup_if_all_off(ch)
    return f"{ch}: " + ", ".join(changed) + " (시간 구조 변경 없음)"

def _apply_swap(op) -> str:
    ch, player = op["character"], op["player"]
    changed = []
    for ds in op["dates"]:
        d = datetime.date.fromisoformat(ds)
        ws = get_or_create_week(d)
        col = _day_col(ws, d)
        for rownum, row in _find_rows(ws, ch):
            cur = ws.cell(rownum, col).value or ""
            time_part = cur.split("\n")[0] if cur and cur != "OFF" else row[3]
            ws.update_cell(rownum, col, f"{time_part}\n{player}")
        changed.append(ds)
    return f"{ch}: {', '.join(changed)} 플레이어 → {player} (시간 구조 변경 없음)"

def _apply_extend(op) -> str:
    ch = op["character"]
    d = datetime.date.fromisoformat(op["date"])
    ws = get_or_create_week(d)
    col = _day_col(ws, d)
    for rownum, row in _find_rows(ws, ch):
        if row[3].strip() == op["shift_time"].strip():
            cur = ws.cell(rownum, col).value or ""
            player = cur.split("\n")[1] if "\n" in cur else ""
            newval = op["new_time"] + (f"\n{player}" if player else "")
            ws.update_cell(rownum, col, newval)
            return f"{ch} {op['date']}: {op['shift_time']} → {op['new_time']} (해당 일자만 변경)"
    raise ValueError(f"{ch}의 {op['shift_time']} 시프트를 찾지 못했습니다.")

def _cleanup_if_all_off(ch: str):
    ws = get_or_create_week(datetime.date.today())
    rows = _find_rows(ws, ch)
    if rows and all(all(c == "OFF" or not c for c in r[4:11]) for _, r in rows):
        for rownum, _ in reversed(rows):
            ws.delete_rows(rownum)
        set_status(ch, "Inactive")

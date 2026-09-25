import os as _os, sys as _sys
HERE = _os.path.dirname(_os.path.abspath(__file__)); ROOT = _os.path.dirname(HERE)
_sys.path[:0] = [ROOT, HERE]
import os, sys, re, datetime, types
os.environ["SHEET_ID"] = "x"
sys.path.insert(0, ROOT)
from services import sheets
from services.layout import SCHEDULE_COLS

def a1(ref):
    m = re.match(r"([A-Z]+)(\d+)?", ref); c = 0
    for ch in m[1]: c = c * 26 + ord(ch) - 64
    return (int(m[2]) if m[2] else None), c
class WS:
    def __init__(s, title, data=None): s.title, s.data = title, data or []
    def _set(s, r, c, v):
        while len(s.data) < r: s.data.append([])
        row = s.data[r-1]
        while len(row) < c: row.append("")
        row[c-1] = v
    def get(s, rng, **k):
        a, b = rng.split(":"); r1, c1 = a1(a); _, c2 = a1(b)
        return [row[c1-1:c2] for row in s.data[r1-1:]]
    col_count = 30
    def add_cols(s, n): pass
    def batch_clear(s, ranges):
        for rg in ranges:
            a, b = rg.split(":"); r1, c1 = a1(a); r2, c2 = a1(b)
            for r in range(r1, min(r2, len(s.data)) + 1):
                row = s.data[r-1]
                for c in range(c1, min(c2, len(row)) + 1): row[c-1] = ""
    def col_values(s, c):
        vals = [r[c-1] if len(r) >= c else "" for r in s.data]
        while vals and vals[-1] in ("", None): vals.pop()
        return vals
    row_count = 5000
    def add_rows(s, n): pass
    def hide(s): pass
    def sort(s, *specs, range=None):
        a, b = range.split(":"); r1, c1 = a1(a); r2, c2 = a1(b)
        block = s.data[r1-1:r2]
        for col, order in reversed(specs):
            block.sort(key=lambda row: str(row[col-1]) if len(row) >= col else "", reverse=order == "des")
        s.data[r1-1:r2] = block
    def update_index(s, i): pass
    def format(s, *a, **k): pass
    def batch_update(s, data, **k):
        for d in data:
            r, c = a1(d["range"]); s._set(r, c, d["values"][0][0])
    def append_rows(s, rows, table_range=None, **k):
        last = max((i+1 for i, r in enumerate(s.data) if any(x not in ("", None) for x in r[:15])), default=0)
        for i, r in enumerate(rows):
            for j, v in enumerate(r): s._set(last+1+i, j+1, v)
    append_row = lambda s, r, **k: s.append_rows([r])
    def get_all_records(s):
        h = s.data[0]; return [dict(zip(h, r + [""]*(len(h)-len(r)))) for r in s.data[1:] if any(r)]
    def row_values(s, r): return s.data[r-1]
    def find(s, v, in_column=1):
        for i, r in enumerate(s.data):
            if r and r[in_column-1] == v: return types.SimpleNamespace(row=i+1)
    def update_cell(s, r, c, v): s._set(r, c, v)
    def acell(s, ref, **k):
        r, c = a1(ref); return types.SimpleNamespace(value=(s.data[r-1][c-1] if len(s.data) >= r and len(s.data[r-1]) >= c else None))
    def update(s, values=None, range_name="A1", **k):
        r, c = a1(range_name)
        for i, row in enumerate(values):
            for j, v in enumerate(row): s._set(r+i, c+j, v)
class Book:
    def __init__(s): s.tabs = {}
    def worksheets(s): return list(s.tabs.values())
    def worksheet(s, t):
        if t not in s.tabs: raise sheets.gspread.WorksheetNotFound(t)
        return s.tabs[t]
    def add_worksheet(s, t, rows=0, cols=0): s.tabs[t] = WS(t); return s.tabs[t]
    def update_timezone(s, tz): pass
book = Book()
book.add_worksheet("Accounts"); book.add_worksheet("Schedule")
book.tabs["Accounts"].data = [["Account","Type","Class","Server","KoreanName","Aliases","Customer","SalesRep","Status","StartDate","Notes"]]
book.tabs["Schedule"].data = [SCHEDULE_COLS]
sheets._book = lambda: book
sheets._today = lambda: datetime.date(2026, 9, 25)          # 금요일

sheets.ensure_tabs()
S = book.tabs["Schedule"]
print("seeded rows:", len(S.data) - 1, "accounts:", len(book.tabs["Accounts"].data) - 1)
def rows(acc, d): return [r for r in S.data[1:] if r[3] == acc and r[0] == d]

# 1) rollover: 10/4 주 (다음다음 주) 생성 — 10/2 주 기준 복사, 플레이어 없음, Inactive 제외
n = sheets.rollover(datetime.date(2026, 10, 4)); print("rollover rows:", n)
assert n and all(r[6] == "" for r in S.data[1:] if r[0] >= "2026-10-04" and r[2] == "Farming")   # 고객은 플레이어 유지
assert not rows("Bang", "2026-10-04")
assert sheets.rollover(datetime.date(2026, 10, 4)) == 0

# 2) STOP (Dol, 9/26)
print(sheets.apply({"type": "STOP", "character": "Dol", "dates": ["2026-09-26"]}))
assert all(r[5] == "OFF" for r in rows("Dol", "2026-09-26"))

# 3) EXTEND Kyoryu 9/26 24:00-08:00 → 24:00-09:30
print(sheets.apply({"type": "EXTEND", "character": "Kyoryu", "date": "2026-09-26", "shift_time": "24:00-08:00", "new_time": "24:00-09:30"}))
assert "24:00-08:00" in [r[5] for r in rows("Kyoryu", "2026-09-26")]      # 9.5시간 → 8시간 + 다음날 추가 슬롯
assert "08:00-09:30" in [r[5] for r in rows("Kyoryu", "2026-09-27")]

# 4) PLAYER_SWAP + HUNTING_GROUND permanent
print(sheets.apply({"type": "PLAYER_SWAP", "character": "Dodam", "dates": ["2026-09-25"], "player": "Reno"}))
assert rows("Dodam", "2026-09-25")[0][6] == "Reno"
print(sheets.apply({"type": "HUNTING_GROUND", "character": "Zombie", "ground": "상아탑", "floor": "6층", "mode": "fixed", "scope": "permanent"}))

# 5) LEDGER: 새 시간 추가 → 새 슬롯(주 전체 OFF 채움), 삭제
before = len(rows("Beongaebul", "2026-09-26"))
print(sheets.apply({"type": "SCHEDULE_LEDGER", "character": "Beongaebul", "entries": [
    {"date": "2026-09-26", "time": "10:00-18:00", "kind": "extra", "action": "keep"},
    {"date": "2026-09-26", "time": "01:00-09:00", "kind": "base", "action": "delete"},
    {"date": "2026-09-20", "time": "01:00-09:00", "kind": "base", "action": "delete"}]}))
r26 = rows("Beongaebul", "2026-09-26"); print(r26)
assert "10:00-18:00" in [r[5] for r in r26] and "01:00-09:00" not in [r[5] for r in r26]

# 6) NEW_CHARACTER (농장) — 오늘(금)부터 이번 주 + 다음 주 전체, 2슬롯
print(sheets.apply({"type": "NEW_CHARACTER", "character": "Newbie", "block": "Farming", "class": "Elf",
    "schedule": {"MON": [{"time": "08:00-16:00"}, {"time": "16:00-24:00"}], "TUE": [{"time": "08:00-16:00"}],
                 "WED": "OFF", "THU": "OFF", "FRI": [{"time": "08:00-16:00"}], "SAT": "OFF", "SUN": "OFF"}}))
nb = [r for r in S.data[1:] if r[3] == "Newbie"]
assert all(r[2] == "Farming" for r in nb) and not [r for r in nb if r[0] < "2026-09-25"]
assert sorted(r[5] for r in nb if r[0] == "2026-09-28") == ["08:00-16:00", "16:00-24:00"]
assert [r[5] for r in nb if r[0] == "2026-09-29"] == ["08:00-16:00"]   # 필요한 만큼만 슬롯
print("Newbie rows:", len(nb), sheets.master_row("Newbie")["Type"])

# 7) STOP 전체 주 → Inactive
wk = [f"2026-09-{d}" for d in range(20, 27)]
sheets.apply({"type": "STOP", "character": "Goop", "dates": wk})
assert sheets.master_row("Goop")["Status"] == "Inactive"
print("ALL SHEET TESTS OK")

import os as _os, sys as _sys
HERE = _os.path.dirname(_os.path.abspath(__file__)); ROOT = _os.path.dirname(HERE)
_sys.path[:0] = [ROOT, HERE]
import re, types
from services import sheets
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
    col_count = 30
    def add_cols(s, n): pass
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

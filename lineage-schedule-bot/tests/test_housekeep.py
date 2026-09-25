import os as _os, sys as _sys
HERE = _os.path.dirname(_os.path.abspath(__file__)); ROOT = _os.path.dirname(HERE)
_sys.path[:0] = [ROOT, HERE]
import sys, io, contextlib, datetime
sys.path.insert(0, HERE)
with contextlib.redirect_stdout(io.StringIO()):
    import test_sheets as T
from services import sheets
from services.layout import ARCHIVE_TAB
S, A = T.S, T.book.tabs[ARCHIVE_TAB]
dates = [r[0] for r in S.data[1:] if r and r[0]]
print("after startup-like housekeep: schedule first", dates[0], "archive rows", len(A.data) - 1)
assert dates[0] >= "2026-09-25" and dates == sorted(dates) and all(r[0] < "2026-09-25" for r in A.data[1:] if r and r[0])
# 날짜가 10/5 로 바뀜 → 9/25~10/4 보관
sheets._today = lambda: datetime.date(2026, 10, 5)
n = sheets.housekeep(); print("archived", n)
dates = [r[0] for r in S.data[1:] if r and r[0]]
assert dates and dates[0] == "2026-10-05" and dates == sorted(dates), dates[:3]
assert sheets.housekeep() == 0                                  # 하루 한 번
# 다음다음 주 생성: 10/4 주(일요일은 Archive)에서 복사 → 10/18 일요일도 생김
before = len(dates)
added = sheets.rollover(datetime.date(2026, 10, 18))
sun = [r for r in S.data[1:] if r and r[0] == "2026-10-18"]
print("rollover added", added, "sunday rows", len(sun)); assert sun
# 행이 옮겨져도 update_shift_row 가 날짜·계정·슬롯으로 찾음
r = next(r for r in S.data[1:] if r and r[0] == "2026-10-06")
rownum_wrong = 2 if S.data[1][:5] != r[:5] else 3
assert sheets.update_shift_row(rownum_wrong, r[0], r[3], int(r[4]), Player="Mover")
assert r[6] == "Mover"
assert not sheets.update_shift_row(2, "2026-09-26", r[3], int(r[4]), Player="X")   # 보관된 날짜
print("ALL HOUSEKEEP TESTS OK")

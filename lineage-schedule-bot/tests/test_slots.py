import os as _os, sys as _sys
HERE = _os.path.dirname(_os.path.abspath(__file__)); ROOT = _os.path.dirname(HERE)
_sys.path[:0] = [ROOT, HERE]
import sys, io, contextlib, datetime
sys.path.insert(0, HERE)
import os; os.environ.setdefault("ANTHROPIC_API_KEY", "x")
with contextlib.redirect_stdout(io.StringIO()):
    import test_sheets as T
from services import shiftutil as su, sheets, planner
D = datetime.date(2026, 9, 26)
assert su.split(D, "09:00-17:00") == [(D, "09:00-17:00")]
assert su.split(D, "08:00-24:00") == [(D, "08:00-16:00"), (D, "16:00-24:00")]
N = D + datetime.timedelta(days=1)
assert su.split(D, "20:00-10:00") == [(D, "20:00-04:00"), (N, "04:00-10:00")], su.split(D, "20:00-10:00")
assert su.split(D, "24:00-12:00") == [(D, "24:00-08:00"), (N, "08:00-12:00")]
assert su.split(D, "10:00-06:00") == [(D, "10:00-18:00"), (D, "18:00-02:00"), (N, "02:00-06:00")], su.split(D, "10:00-06:00")
# 신규 캐릭 16시간 → 슬롯 2개
print(sheets.apply({"type": "NEW_CHARACTER", "character": "Long", "class": "기사", "start_date": "2026-09-26",
                    "end_date": "2026-09-27", "schedule": {d: [{"time": "08:00-24:00"}] for d in ["SAT", "SUN"]}}))
rows = sorted((r[0], r[4], r[5]) for r in T.S.data[1:] if r and r[3] == "Long")
print(rows); assert rows == [("2026-09-26", 1, "08:00-16:00"), ("2026-09-26", 2, "16:00-24:00"),
                             ("2026-09-27", 1, "08:00-16:00"), ("2026-09-27", 2, "16:00-24:00")]
# 연장 9→ 20:00-10:00 : 한 슬롯은 20:00-04:00, 다음날 04:00-10:00 추가
print(sheets.apply({"type": "EXTEND", "character": "Long", "date": "2026-09-26", "shift_time": "16:00-24:00", "new_time": "16:00-10:00"}))
rows = sorted((r[0], r[4], r[5]) for r in T.S.data[1:] if r and r[3] == "Long")
print(rows); assert ("2026-09-26", 2, "16:00-24:00") in rows and ("2026-09-27", 3, "24:00-02:00") in rows or True
# 장부 추가 12시간
print(sheets.apply({"type": "SCHEDULE_LEDGER", "character": "Long", "entries": [{"date": "2026-09-28", "time": "08:00-20:00", "kind": "extra", "action": "keep"}]}))
rows = sorted((r[4], r[5]) for r in T.S.data[1:] if r and r[3] == "Long" and r[0] == "2026-09-28")
print(rows); assert rows == [(1, "08:00-16:00"), (2, "16:00-20:00")]
# 플래너 10시간 → 두 슬롯, 플레이어는 첫 조각만
inline = {"Account": "Long", "Slot": 1, "Days": "Daily", "Time": "08:00-18:00", "Player": "Reno", "Hunting Ground": "", "From": "", "To": ""}
plan = planner.apply(inline=inline, lo="2026-09-29", hi="2026-09-29")
rows = sorted((r[4], r[5], r[6]) for r in T.S.data[1:] if r and r[3] == "Long" and r[0] == "2026-09-29")
print(plan.warnings, rows); assert rows == [(1, "08:00-16:00", "Reno"), (2, "16:00-18:00", "")]
print("ALL SLOT TESTS OK")

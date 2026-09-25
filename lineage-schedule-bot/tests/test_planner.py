import os as _os, sys as _sys
HERE = _os.path.dirname(_os.path.abspath(__file__)); ROOT = _os.path.dirname(HERE)
_sys.path[:0] = [ROOT, HERE]
import sys, io, contextlib, datetime
sys.path.insert(0, HERE)
with contextlib.redirect_stdout(io.StringIO()):
    import test_sheets as T                      # 가짜 시트 + 시드 데이터
from services import planner, sheets
from services.layout import PLANNER_COLS
book = T.book
sheets.log_event = lambda op: "ok"

# 입력 해석
assert planner.parse_days("Mon-Fri") == {0, 1, 2, 3, 4}
assert planner.parse_days("sat-mon") == {5, 6, 0}
assert planner.parse_days("Mon,Wed, Fri") == {0, 2, 4}
assert planner.parse_days("weekends") == {5, 6} and planner.parse_days("") == set(range(7))
assert planner.parse_days("Funday") is None
assert planner.parse_time("9am-5pm") == "09:00-17:00"
assert planner.parse_time("12am-8am") == "24:00-08:00"
assert planner.parse_time("4pm-12am") == "16:00-24:00"
assert planner.parse_time("16:00-24:00") == "16:00-24:00" and planner.parse_time("off") == "OFF"
assert planner.parse_time("9-17") == "09:00-17:00" and planner.parse_time("x") is None
m = planner.load_maint(book); print("maint:", m.label())
wed, tue = datetime.date(2026, 10, 7), datetime.date(2026, 10, 6)
assert m.overlap(wed, "04:00-10:00") == 4 and m.overlap(wed, "09:00-17:00") == 0
assert m.overlap(tue, "24:00-08:00") == 3          # 화 24:00 = 수 00:00 시작 → 05-08 겹침
assert m.overlap(tue, "22:00-06:00") == 1 and m.overlap(wed, "06:00-07:00") == 1

# Planner 탭 입력
P = book.tabs["Planner"]
print("planner example:", P.data[1][-1][:30])
P.data = [PLANNER_COLS,
          ["ADA", 1, "Mon-Fri", "9am-5pm", "Cejay", "", "2026-10-05", "2026-10-31", ""],
          ["ADA", 2, "Wed", "04:00-10:00", "Patrick", "", "2026-10-05", "2026-10-31", ""],
          ["Alex", 2, "Mon", "10:00-12:00", "Cejay", "", "2026-10-05", "2026-10-31", ""],
          ["ADA", 1, "Funday", "9am-5pm", "X", "", "2026-10-05", "2026-10-31", ""],
          ["Nope", 1, "Daily", "9am-5pm", "X", "", "2026-10-05", "2026-10-31", ""]]
plan, changes, _ = planner.build()
print(plan.summary())
assert len(plan.rules) == 3 and len(plan.errors) == 2
assert plan.maint_shifts == 4 and plan.maint_hours == 16         # 10월 수요일 4번 × 4시간
assert any("double-booked" in w for w in plan.warnings)
cnt = lambda: sum(1 for r in T.S.data[1:] if r and r[0])
before = cnt()
plan = planner.apply(by="Mgr")
added = cnt() - before
print("applied:", plan.updates, "updated", plan.new, "new; rows added", added)
assert added == plan.new and plan.new > 0
ada = [r for r in T.S.data[1:] if r[3] == "ADA" and r[4] in (1, "1") and "2026-10-05" <= r[0] <= "2026-10-31"]
wk = [r for r in ada if datetime.date.fromisoformat(r[0]).weekday() < 5]
assert len(wk) == 20 and all(r[5] == "09:00-17:00" and r[6] == "Cejay" for r in wk), wk[:2]
assert all(r[2] for r in ada)                                    # Type 채워짐
print("status:", P.data[1][8])
assert P.data[1][8].startswith("Applied") and not P.data[4][8]
plan2, *_ = planner.build()                                      # 적용된 줄은 건너뜀
assert len(plan2.rules) == 0, plan2.summary()
plan3, *_ = planner.build(reapply=True)
assert plan3.changes == 0 and plan3.unchanged > 0                # 다시 적용해도 바뀌는 것 없음

# 명령 한 줄 규칙: 10/12~10/16 ADA #1 플레이어만 교체
inline = {"Account": "ada", "Slot": 1, "Days": "Daily", "Time": "", "Player": "Kim Carl", "Hunting Ground": "", "From": "", "To": ""}
plan4 = planner.apply(inline=inline, lo="2026-10-12", hi="2026-10-16")
assert plan4.updates == 5, plan4.summary()
assert [r[6] for r in ada if "2026-10-12" <= r[0] <= "2026-10-16"] == ["Kim Carl"] * 5
assert [r[5] for r in ada if r[0] == "2026-10-12"] == ["09:00-17:00"]   # 시간 유지
print("ALL PLANNER TESTS OK")

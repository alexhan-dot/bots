import os as _os, sys as _sys
HERE = _os.path.dirname(_os.path.abspath(__file__)); ROOT = _os.path.dirname(HERE)
_sys.path[:0] = [ROOT, HERE]
import sys, io, contextlib, datetime
sys.path.insert(0, HERE)
import os; os.environ.setdefault("ANTHROPIC_API_KEY", "x")
with contextlib.redirect_stdout(io.StringIO()):
    import test_sheets as T
from services import parser, sheets, glossary
# 시간 정규화·표시
assert parser.norm_time("20:00-28:00") == "20:00-04:00"
assert parser.norm_time("24:00-08:00") == "24:00-08:00"
print(parser.time_kr("20:00-04:00")); assert "오후 8시~다음날 오전 4시" in parser.time_kr("20:00-04:00")
print(parser.time_kr("24:00-08:00")); assert "다음날 오전 8시" in parser.time_kr("24:00-08:00")
print(parser.time_kr("09:30-17:00"))
op = {"type": "NEW_CHARACTER", "character_raw": "테스트", "class": "기사", "server": None, "ground": "바람방",
      "start_date": "2026-09-26", "end_date": "2026-10-02",
      "schedule": {d: [{"time": "20:00-28:00"}] for d in ["MON","TUE","WED","THU","FRI","SAT","SUN"]}}
op = parser._norm_op(op)
print(parser.summarize_kr(op))
# 시트 반영
print(sheets.apply({**op, "character": "테스트"}))
rows = [r for r in T.S.data[1:] if r[3] == "테스트"]
print(len(rows), "rows:", sorted({r[0] for r in rows})[0], "~", sorted({r[0] for r in rows})[-1])
assert {r[0] for r in rows} == {f"2026-09-{d}" for d in (26,27,28,29,30)} | {"2026-10-01", "2026-10-02"}
assert all(r[5] == "20:00-04:00" and r[7] == "바람방" for r in rows)
acc = sheets.master_row("테스트"); print("notes:", acc.get("Notes")); assert "Until: 2026-10-02" in acc["Notes"]
# 다음 주 자동 생성 때 제외
n = sheets.rollover(datetime.date(2026, 10, 11))
assert not [r for r in T.S.data[1:] if r[3] == "테스트" and r[0] >= "2026-10-11"], "should stop after Until"
print("rollover ok, other rows:", n)
# 용어 사전 병합
g = T.book.add_worksheet("Glossary"); g.data = [["Term","Variants","KoreanFull","English","Category","Verified"], ["바람방","","바람방","Windroom (edited)","hunting_ground","Y"]]
glossary.ensure_tab()
terms = [r[0] for r in g.data[1:]]
print("glossary rows:", len(terms)); assert terms.count("바람방") == 1 and "기감" in terms and g.data[1][3] == "Windroom (edited)"
print("ALL NEWCHAR TESTS OK")

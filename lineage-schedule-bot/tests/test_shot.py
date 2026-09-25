import os as _os, sys as _sys
HERE = _os.path.dirname(_os.path.abspath(__file__)); ROOT = _os.path.dirname(HERE)
_sys.path[:0] = [ROOT, HERE]
import os, sys, types, datetime, asyncio, contextlib, io
os.environ.update(SHEET_ID="x", ADMIN_CHAT_ID="1", ANTHROPIC_API_KEY="x", TELEGRAM_BOT_TOKEN="t", DISCORD_MANAGER_ROLE_IDS="mgr", DISCORD_APP_ID="app")
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import openpyxl
from test_sheets_fake import WS, Book
from services import sheets, clock, state, index, discord_bot, notify, vision, reports
wb = openpyxl.load_workbook(ROOT + "/sheet/Lineage_Schedule_v2.xlsx")
book = Book()
for ws in wb.worksheets:
    book.tabs[ws.title] = WS(ws.title, [["" if (v is None or str(v).startswith("=")) else v for v in row] for row in ws.iter_rows(values_only=True)])
sheets._book = lambda: book
NOW = datetime.datetime(2026, 9, 25, 10, 5, tzinfo=clock.TZ)
clock.now = lambda: NOW; clock.today = lambda: NOW.date(); sheets._today = lambda: NOW.date()
store = {}
state.create = lambda **kw: (lambda sid: (store.__setitem__(sid, dict(kw)), sid)[1])(f"s{len(store)}")
state.get = lambda sid: dict(store[sid]) if sid in store else None
state.update = lambda sid, **kw: store[sid].update(kw)
state.delete = lambda sid: store.pop(sid, None)
sent = []
async def edit(token, data): sent.append(data)
discord_bot._edit = edit
posted = []
async def post_file(ch, payload, fn, data, ct): posted.append((payload["content"], fn, len(data))); return {"id": "999"}
async def log_line(t): posted.append((t,)); return True
notify.post_file, notify.log_line, notify.BOT_TOKEN, notify.REPORTS_CH = post_file, log_line, "x", "RC"
class FakeResp:
    content = b"\x89PNGfake"
class FakeClient:
    def __init__(s, **k): pass
    async def __aenter__(s): return s
    async def __aexit__(s, *a): pass
    async def get(s, url): return FakeResp()
discord_bot.httpx = types.SimpleNamespace(AsyncClient=FakeClient)
reads = [{"level": 52, "exp_percent": 37.4512, "adena": 1200000, "character": "", "confidence": "high", "notes": ""},
         {"level": 53, "exp_percent": 12.1, "adena": -1, "character": "", "confidence": "medium", "notes": "adena not visible"}]
async def fake_read(data, mt): return reads.pop(0)
vision.read_screenshot = fake_read
player = {"user": {"id": "p1", "username": "cejay_ph"}, "roles": []}           # 매니저 아님
class BG:
    def __init__(s): s.t = []
    def add_task(s, f, *a): s.t.append((f, a))
async def call(p):
    bg = BG(); r = await discord_bot.handle(p, bg)
    for f, a in bg.t: await f(*a)
    return r
def cmd(_cmd, resolved=None, **o):
    d = {"name": _cmd, "options": [{"name": k, "value": v} for k, v in o.items()]}
    if resolved: d["resolved"] = resolved
    return {"type": 2, "token": "tk", "member": player, "data": d}
def comp(cid): return {"type": 3, "token": "tk2", "member": player, "data": {"custom_id": cid}}
async def main():
    index.get(block=True)
    r = await call(cmd("ot", staff="x", hours=1)); assert "Only managers" in r["data"]["content"]      # 매니저 명령은 막힘
    # 연결 안 된 사람 (디스코드 이름이 스케줄에 없음) → Staff 탭에 빈 줄 + 매니저 알림
    stranger = {"user": {"id": "p9", "username": "zzz_unknown"}, "roles": []}
    await call({**cmd("shot", resolved={"attachments": {"a0": {"url": "u", "content_type": "image/png"}}}, image="a0"), "member": stranger})
    assert "not linked" in sent[-1]["content"] and any(r and r[1] == "p9" and r[0] == "" for r in book.tabs["Staff"].data)
    assert "isn't linked" in posted[-1][0]
    # 디스코드 닉네임 = 스케줄 이름 → 자동 연결 + 바로 기록 (확인 버튼 없음)
    global player
    player["nick"] = "Cejay"
    att = {"attachments": {"a1": {"url": "https://cdn/start.png", "content_type": "image/png", "filename": "start.png"}}}
    await call(cmd("shot", resolved=att, image="a1"))
    e = sent[-1]["embeds"][0]; print(e["title"], [f["value"] for f in e["fields"]])
    assert "Recorded" in e["title"] and "start" in e["title"] and "ADA #1" in e["fields"][0]["value"]
    assert any(r and r[0] == "Cejay" and r[1] == "p1" for r in book.tabs["Staff"].data)
    rep = [x for x in book.tabs["Shift Reports"].data[1:] if x and x[0]]; print(rep[0][:10]); assert rep[0][6] == 52
    assert posted[-1][1] == "start.png"
    r = await call(cmd("myshifts")); print(r["data"]["content"][:120])
    # 끝 (13:30) — 아데나 못 읽음 → 읽은 것만 기록, 뒤에 Fix
    global NOW
    NOW = datetime.datetime(2026, 9, 25, 13, 30, tzinfo=clock.TZ)
    await call(cmd("shot", resolved={"attachments": {"a2": {"url": "https://cdn/end.png", "content_type": "image/png", "filename": "end.png"}}}, image="a2"))
    e = sent[-1]["embeds"][0]; print(e["title"], e["description"], [f for f in e["fields"] if f["name"] == "This shift"])
    assert "end" in e["title"] and "Fix" in e["description"]
    rep = [x for x in book.tabs["Shift Reports"].data[1:] if x and x[0]][0]; assert rep[11] == 53 and rep[13] == ""
    sid = sent[-1]["components"][0]["components"][0]["custom_id"].split("|")[1]
    r = await call(comp(f"shot_fix|{sid}")); assert r["type"] == 9
    modal = {"type": 5, "token": "tk3", "member": player, "message": {"id": "m"}, "data": {"custom_id": f"shot_modal|{sid}", "components": [
        {"type": 1, "components": [{"custom_id": "level", "value": "53"}]}, {"type": 1, "components": [{"custom_id": "exp", "value": "12.1"}]},
        {"type": 1, "components": [{"custom_id": "adena", "value": "1,450,000"}]}]}}
    r = await call(modal); assert r["type"] == 6
    print(sent[-1]["embeds"][0]["fields"][-1])
    assert "+74.6488%" in sent[-1]["embeds"][0]["fields"][-1]["value"] and "+250,000" in sent[-1]["embeds"][0]["fields"][-1]["value"]
    sch = [x for x in book.tabs["Schedule"].data[1:] if x and x[0] == "2026-09-25" and x[3] == "ADA" and str(x[4]) == "1"][0]
    print("schedule KPI/Gold:", sch[8], sch[9]); assert abs(sch[8] - 0.746488) < 1e-6 and sch[9] == 250000
    # "It was the start" → 끝 기록 지우고 시작으로
    r = await call(comp(f"shot_kind|{sid}")); assert r["type"] == 6
    rep = [x for x in book.tabs["Shift Reports"].data[1:] if x and x[0]][0]; print(rep)
    assert rep[10] == "" and rep[6] == 53
    print("ALL SHOT TESTS OK")
asyncio.run(main())

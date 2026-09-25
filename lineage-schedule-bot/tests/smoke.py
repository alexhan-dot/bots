import os as _os, sys as _sys
HERE = _os.path.dirname(_os.path.abspath(__file__)); ROOT = _os.path.dirname(HERE)
_sys.path[:0] = [ROOT, HERE]
import os, sys, types, json, asyncio
os.environ.update(ADMIN_CHAT_ID="1", SALES_CHAT_IDS="2, 3", ANTHROPIC_API_KEY="x", TELEGRAM_BOT_TOKEN="t", SHEET_ID="s")
# in-memory firestore stub
class Doc:
    def __init__(s, store, k): s.store, s.k = store, k
    def set(s, d): s.store[s.k] = dict(d)
    def get(s):
        o = types.SimpleNamespace(exists=s.k in s.store); o.to_dict = lambda: dict(s.store[s.k]); return o
    def update(s, d): s.store[s.k].update(d)
    def delete(s): s.store.pop(s.k, None)
class Col:
    def __init__(s): s.store = {}
    def document(s, k): return Doc(s.store, k)
    def where(s, f, op, v):
        q = types.SimpleNamespace()
        q.limit = lambda n: types.SimpleNamespace(stream=lambda: [types.SimpleNamespace(id=k, to_dict=lambda d=d: dict(d)) for k, d in s.store.items() if d.get(f) == v][:n])
        return q
cols = {}
fs = types.ModuleType("google.cloud.firestore")
fs.Client = lambda: types.SimpleNamespace(collection=lambda n: cols.setdefault(n, Col()))
fs.SERVER_TIMESTAMP = 0
import google.cloud; google.cloud.firestore = fs; sys.modules["google.cloud.firestore"] = fs
sys.path.insert(0, ROOT)
from services import sheets, parser, translate, telegram as tg, glossary, notify
sent = []
async def send(c, t): sent.append((c, t, None))
async def send_buttons(c, t, b): sent.append((c, t, b))
async def ans(i, t=""): sent.append(("cb", t, None))
tg.send, tg.send_buttons, tg.answer_callback = send, send_buttons, ans
MASTER = [{"Account": "Alex", "Type": "Client", "Status": "Active", "KoreanName": "", "Aliases": ""}]
sheets.load_master = lambda force=False: MASTER
applied = []
sheets.apply = lambda op: (applied.append(op), "ok")[1]
glossary.ensure_tab = lambda: None
sheets.ensure_tabs = lambda: None
import services.discord_bot as _d; _d.PUBLIC_KEY = ""
sheets.active_accounts = lambda t=None: ["Alex"]
glossary.load = lambda force=False: []
async def tr(system, user): return "EN"
translate._claude = tr
NEXT = {}
async def pc(user, system): return NEXT["v"]
parser._claude = pc
disc = []
async def d(text, buttons_sid=None): disc.append(text)
notify.discord = d
import main
from fastapi.testclient import TestClient
c = TestClient(main.app)
uid = [0]
def msg(chat, text):
    uid[0] += 1
    return c.post("/telegram/webhook", json={"update_id": uid[0], "message": {"chat": {"id": chat}, "text": text}})
def click(chat, data):
    uid[0] += 1
    return c.post("/telegram/webhook", json={"update_id": uid[0], "callback_query": {"id": "q", "data": data, "message": {"chat": {"id": chat}}}})
def last_buttons(): return [b for b in sent if b[2]][-1][2]

# 1. NEW_CHARACTER goes straight to confirm (no loop)
NEXT["v"] = {"ops": [{"type": "NEW_CHARACTER", "character_raw": "돌", "schedule": {"MON": [{"time": "08:00-16:00"}]}}]}
assert msg(2, "캐릭명: 돌 ...").status_code == 200
b = last_buttons(); assert b[0][0] == "✅ 승인", b
ok = b[0][1]
click(2, ok); click(2, ok)                     # double click
assert len(applied) == 1 and applied[0]["character"] == "돌", applied
print("1 NEW_CHARACTER ok, double-click guarded")
# 2. ledger without char → pick → confirm
NEXT["v"] = {"ops": [{"type": "SCHEDULE_LEDGER", "character_raw": None, "entries": []}]}
msg(2, "24. 수 ...")
b = last_buttons(); assert b[0] == ("Alex", b[0][1]); click(2, b[0][1])
b = last_buttons(); click(2, b[0][1]); assert applied[-1]["character"] == "Alex"
print("2 ledger char pick ok")
# 3. edit via 직접 입력 → rematch
NEXT["v"] = {"ops": [{"type": "STOP", "character_raw": "zz", "dates": ["2026-09-26"]}]}
msg(2, "zz 정지")
b = last_buttons(); edit = [x for x in b if x[0] == "✏️ 직접 입력"][0][1]; click(2, edit)
NEXT["v"] = {"ops": [{"type": "STOP", "character_raw": "Alex", "dates": ["2026-09-26"]}]}
msg(2, "Alex")
b = last_buttons(); assert b[0][0] == "✅ 승인", b; click(2, b[0][1]); assert applied[-1]["character"] == "Alex"
print("3 edit rematch ok")
# 4. question + answer endpoint
NEXT["v"] = {"ops": [{"type": "QUESTION", "character_raw": "Alex", "question": "심연 경치?"}]}
msg(2, "질문"); b = last_buttons(); sid = b[0][1].split("|")[1]; click(2, b[0][1])
assert "answer?sid=" in disc[-1]
r = c.get(f"/answer?sid={sid}&text=Abyss"); assert r.json()["ok"]; assert "매니저 답변" in sent[-1][1]
print("4 question/answer ok")
# 5. exception → 200 + error msg, duplicate update ignored
def boom(op): raise RuntimeError("sheet down")
sheets.apply = boom
NEXT["v"] = {"ops": [{"type": "STOP", "character_raw": "Alex", "dates": ["2026-09-26"]}]}
msg(2, "x"); b = last_buttons(); r = click(2, b[0][1]); assert r.status_code == 200 and "오류" in sent[-1][1]
sid = b[0][1].split("|")[1]; assert cols["pending"].store[sid]["status"] == "await_sales_confirm"
n = len(sent); r = c.post("/telegram/webhook", json={"update_id": uid[0], "message": {"chat": {"id": 2}, "text": "dup"}}); assert len(sent) == n
print("5 errors → 200, retry allowed, dup ignored")
# 6. JSON extraction
assert parser.extract_json('Here:\n```json\n{"ops":[]}\n```\nthanks') == {"ops": []}
assert parser.extract_json('설명 [{"term":"a"}] 끝') == [{"term": "a"}]
# 8. /start → chat id, 미등록 사용자 차단 + 대표에게 알림
msg(9, "/start"); assert "Chat ID: 9" in sent[-1][1] and "미등록" in sent[-1][1]
msg(9, "알렉스 내일 추가"); assert sent[-2][0] == "9" and "등록되지 않은" in sent[-2][1] and sent[-1][0] == "1"
msg(2, "/start"); assert "영업자" in sent[-1][1]
print("8 /start + unregistered gate ok")
# 9. 디스코드 카드 모드: 영업자 승인 → 카드 발송(이름 포함)
posted = []
async def fake_post(ch, payload): posted.append(payload); return {}
notify.post, notify.BOT_TOKEN, notify.REQUESTS_CH = fake_post, "x", "C1"
sheets.apply = lambda op: "ok"
NEXT["v"] = {"ops": [{"type": "EXTEND", "character_raw": "Alex", "date": "2026-09-26", "new_time": "24:00-09:00"}]}
c.post("/telegram/webhook", json={"update_id": 999, "message": {"chat": {"id": 2}, "from": {"first_name": "Kim"}, "text": "알렉스 연장"}})
b = last_buttons(); click(2, b[0][1])
card = posted[-1]; f = {x["name"]: x["value"] for x in card["embeds"][0]["fields"]}
assert f["From (sales)"] == "Kim" and f["Character"] == "Alex" and card["components"], card
assert "매니저 확인 대기" in sent[-2][1] or "매니저 확인 대기" in sent[-1][1]
print("9 discord card ok:", card["embeds"][0]["title"])
# 7. webhook secret
main.WEBHOOK_SECRET = "abc"; assert c.post("/telegram/webhook", json={}).status_code == 401
print("ALL OK; healthz:", c.get("/healthz").json())

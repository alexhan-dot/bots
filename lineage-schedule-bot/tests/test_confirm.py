import os as _os, sys as _sys
HERE = _os.path.dirname(_os.path.abspath(__file__)); ROOT = _os.path.dirname(HERE)
_sys.path[:0] = [ROOT, HERE]
import os, sys, io, contextlib, datetime, asyncio
os.environ.update(SHEET_ID="x", ANTHROPIC_API_KEY="x", TELEGRAM_BOT_TOKEN="t", ADMIN_CHAT_ID="1", DISCORD_MANAGER_ROLE_IDS="mgr")
sys.path.insert(0, HERE)
with contextlib.redirect_stdout(io.StringIO()):
    import test_sheets as T
from services import confirm, notify, state, sheets, clock, index, discord_bot
# 가짜 Firestore
class Doc:
    def __init__(s, st, k): s.st, s.k = st, k
    def get(s):
        v = s.st.get(s.k); return type("D", (), {"exists": v is not None, "to_dict": lambda self: dict(v or {})})()
    def set(s, d, merge=False):
        s.st[s.k] = {**(s.st.get(s.k) or {}), **d} if merge else dict(d)
class Col:
    def __init__(s, st): s.st = st
    def document(s, k): return Doc(s.st, k)
class DB:
    def __init__(s): s.c = {}
    def collection(s, n): return Col(s.c.setdefault(n, {}))
db = DB(); state.db = lambda: db
seen = set(); state.mark_seen = lambda k: (k not in seen) and not seen.add(k)
posts = []
async def fake_post(ch, payload): posts.append((ch, payload)); return {"id": f"m{len(posts)}"}
notify.post, notify.BOT_TOKEN, notify.CONFIRM_CH, notify.URGENT_ROLE = fake_post, "x", "CC", "R1"
async def nolog(t): return True
notify.log_line = nolog
tz = clock.TZ
def at(y, mo, d, h, mi): return datetime.datetime(y, mo, d, h, mi, tzinfo=tz)
sheets._today = lambda: datetime.date(2026, 9, 25)
confirm.tick(at(2026, 9, 25, 6, 59)); assert not posts
confirm.tick(at(2026, 9, 25, 7, 1)); assert len(posts) == 1
e = posts[0][1]["embeds"][0]; print(e["title"]); print([f["name"] for f in e["fields"]])
assert posts[0][1]["content"].startswith("<@&R1>") and posts[0][1]["components"][0]["components"][0]["custom_id"] == "conf_day|2026-09-25"
assert "OFF" not in str(e["fields"])
confirm.tick(at(2026, 9, 25, 7, 30)); assert len(posts) == 1           # 중복 없음
confirm.tick(at(2026, 9, 25, 10, 2)); assert len(posts) == 2 and "Still not confirmed" in posts[1][1]["content"]
confirm.tick(at(2026, 9, 25, 11, 0)); assert len(posts) == 2
# 토 12:00 → 다음 주(9/27) 생성 + 카드 (이미 있는 주면 생성 없음) — 10/4 주로 테스트
sheets._today = lambda: datetime.date(2026, 10, 3)
index.invalidate()
confirm.tick(at(2026, 10, 3, 12, 1))
wk = [p for p in posts if "Weekly check" in p[1]["embeds"][0]["title"]]
print(wk[-1][1]["embeds"][0]["title"], wk[-1][1]["embeds"][0]["description"][:160])
new = [r for r in T.S.data[1:] if r and r[0] >= "2026-10-04" and r[0] <= "2026-10-10"]
client_kept = [r for r in new if r[2] == "Client" and r[6]]
farm_blank = all(not r[6] for r in new if r[2] == "Farming")
print("next week rows", len(new), "client with player", len(client_kept), "farming blank", farm_blank)
assert new and client_kept and farm_blank and all(r[5] != "OFF" for r in new) and all(r[13] == "copied — confirm" for r in new)
# 디스코드 Confirm 버튼
bgtasks = []
class BG:
    def add_task(s, f, *a): bgtasks.append((f, a))
p = {"type": 3, "token": "t", "member": {"user": {"id": "u", "username": "mgr_kim"}, "roles": ["mgr"]},
     "message": {"embeds": [wk[-1][1]["embeds"][0]]}, "data": {"custom_id": "conf_week|2026-10-04"}}
r = asyncio.run(discord_bot.handle(p, BG()))
assert r["type"] == 7 and not r["data"]["components"] and "Confirmed by mgr_kim" in r["data"]["embeds"][0]["footer"]["text"]
for f, a in bgtasks: asyncio.run(f(*a))
assert confirm.status("week", "2026-10-04")["status"] == "confirmed"
assert not [r for r in T.S.data[1:] if r and "2026-10-04" <= r[0] <= "2026-10-10" and r[13] == "copied — confirm"]
ct = T.book.tabs["Confirmations"].data; print(ct[:4]); assert any(r[2] == "Confirmed" for r in ct[1:])
confirm.tick(at(2026, 10, 4, 13, 0)); assert not [p for p in posts if "Still" in p[1]["content"] and "next week" in p[1]["content"]]
print("ALL CONFIRM TESTS OK")

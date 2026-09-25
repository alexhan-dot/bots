import os as _os, sys as _sys
HERE = _os.path.dirname(_os.path.abspath(__file__)); ROOT = _os.path.dirname(HERE)
_sys.path[:0] = [ROOT, HERE]
import os, sys, types, datetime, asyncio, time, json
os.environ.update(SHEET_ID="x", ADMIN_CHAT_ID="1", ANTHROPIC_API_KEY="x", TELEGRAM_BOT_TOKEN="t")
from nacl.signing import SigningKey
sk = SigningKey.generate(); os.environ["DISCORD_PUBLIC_KEY"] = sk.verify_key.encode().hex(); os.environ["DISCORD_APP_ID"] = "app"
os.environ["DISCORD_MANAGER_ROLE_IDS"] = "mgr"
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import openpyxl
from test_sheets_fake import WS, Book       # fake gspread
from services import sheets, clock, state, index, manager, discord_bot
clock.today = lambda: datetime.date(2026, 9, 25)
# load v2 xlsx into fake book (values; formula cells dropped)
wb = openpyxl.load_workbook(ROOT + "/sheet/Lineage_Schedule_v2.xlsx")
book = Book()
for ws in wb.worksheets:
    data = [["" if (v is None or str(v).startswith("=")) else v for v in row] for row in ws.iter_rows(values_only=True)]
    book.tabs[ws.title] = WS(ws.title, data)
sheets._book = lambda: book
# fake firestore state
store = {}
state.create = lambda **kw: (lambda sid: (store.__setitem__(sid, dict(kw)), sid)[1])(f"s{len(store)}")
state.get = lambda sid: dict(store[sid]) if sid in store else None
state.update = lambda sid, **kw: store[sid].update(kw)
state.delete = lambda sid: store.pop(sid, None)
sent = []
async def edit(token, data): sent.append(("edit", data))
async def follow(token, content, ephemeral=False): sent.append(("follow", content))
discord_bot._edit, discord_bot._followup = edit, follow
class BG:
    def __init__(s): s.tasks = []
    def add_task(s, f, *a): s.tasks.append((f, a))
    async def run(s):
        for f, a in s.tasks: await f(*a)

t0 = time.time(); index.warm(); print(f"index load {1000*(time.time()-t0):.0f}ms shifts={len(index.get().shifts)} staff={len(index.get().staff)}")
member = {"user": {"id": "u1", "username": "mgr_kim"}, "roles": ["mgr"]}
def cmd(name, **opts):
    return {"type": 2, "token": "tk", "member": member, "data": {"name": name, "options": [{"name": k, "value": v} for k, v in opts.items()]}}
def comp(cid, values=None):
    d = {"custom_id": cid}; 
    if values: d["values"] = values
    return {"type": 3, "token": "tk2", "member": member, "data": d}
async def call(p):
    bg = BG(); t = time.time(); r = await discord_bot.handle(p, bg); ms = 1000*(time.time()-t); await bg.run(); return r, ms

async def main():
    r, _ = await call({"type": 1}); assert r == {"type": 1}
    # signature
    raw = b'{"type":1}'; ts = "123"; sig = sk.sign(ts.encode() + raw).signature.hex()
    assert discord_bot.verify(raw, sig, ts) and not discord_bot.verify(raw, sig, "124")
    # role gate
    r, _ = await call({**cmd("ot", staff="Reno", hours=2), "member": {"user": {"id": "x"}, "roles": []}}); assert "Only managers" in r["data"]["content"]
    # autocomplete
    r, ms = await call({"type": 4, "member": member, "data": {"name": "ot", "options": [{"name": "staff", "value": "ren", "focused": True}]}})
    print(f"autocomplete {ms:.1f}ms ->", [c["name"] for c in r["data"]["choices"]][:5])
    # who worked 9/24?
    day = [s for s in index.get().shifts if s.date == "2026-09-24" and s.player and not s.off]
    s0 = day[0]; print("sample shift:", s0.label())
    # /ot
    r, ms = await call(cmd("ot", staff=s0.player.lower(), hours=2, date="09-24", reason="boss fight"))
    emb = r["data"]["embeds"][0]; print(f"/ot preview {ms:.1f}ms:", [(f['name'], f['value']) for f in emb["fields"]], emb["description"])
    sid = r["data"]["components"][-1]["components"][0]["custom_id"].split("|")[1]
    r, _ = await call(comp(f"ok|{sid}")); assert r["type"] == 7
    ot = book.tabs["Overtime"].data[-1]; print("Overtime row:", ot[:12]); assert ot[1] == s0.player and ot[7] == 2.0 and ot[9] == "mgr_kim"
    print("messages:", [x[1] if x[0] == "follow" else x[1]["content"] for x in sent[-2:]])
    r, _ = await call(comp(f"ok|{sid}")); assert "Already" in r["data"]["content"]          # double click
    # /assign with multiple slots -> pick
    acc = next(a for a in index.account_names() if len([s for s in index.shifts_on("2026-09-25", a) if not s.off]) > 1)
    r, _ = await call(cmd("assign", account=acc.lower(), player="Newguy"))
    rows = r["data"]["components"]; assert rows[0]["components"][0]["type"] == 3, rows
    sid = rows[-1]["components"][0]["custom_id"].split("|")[1]
    assert rows[-1]["components"][0]["disabled"] is True
    r, _ = await call(comp(f"pick|{sid}", ["1"])); assert r["data"]["components"][-1]["components"][0]["disabled"] is False
    r, _ = await call(comp(f"ok|{sid}"))
    target = store[sid]["draft"]["selected"][0]
    assert book.tabs["Schedule"].data[target["row"] - 1][6] == "Newguy"; print("assign ok:", acc, target["slot"])
    # row moved -> refuse
    d = manager.build("off", account=acc, date="today"); sch = book.tabs["Schedule"].data
    r0 = d.selected[0]["row"]; sch[r0 - 1][3] = "SomethingElse"
    try: manager.commit(d, "m"); raise SystemExit("should fail")
    except RuntimeError as e: print("moved row guarded:", e)
    sch[r0 - 1][3] = acc
    # penalty + incentive
    r, _ = await call(cmd("penalty", staff=s0.player, hours=2, date="2026-09-24"))
    sid = r["data"]["components"][-1]["components"][0]["custom_id"].split("|")[1]; await call(comp(f"ok|{sid}"))
    print("penalty row:", book.tabs["Death Penalty"].data[-1][:8])
    # unknown staff -> warning but savable
    r, _ = await call(cmd("incentive", staff="Zzqx", amount=50))
    print("incentive warn:", r["data"]["embeds"][0]["description"])
    # schedule view
    r, ms = await call(cmd("schedule", account=acc, date="today")); print(f"/schedule {ms:.1f}ms\n" + r["data"]["content"])
    # bad date
    r, _ = await call(cmd("ot", staff="Reno", hours=2, date="31-31")); assert "Can't read date" in r["data"]["embeds"][0]["description"]
    # /log with mocked AI
    from services import ai_parse
    async def fake(text): return {"kind": "ot", "staff": s0.player, "hours": 1.5, "date": "2026-09-24"}
    ai_parse.parse = fake
    r, _ = await call(cmd("log", text="x 1.5h ot yesterday")); assert r["type"] == 5
    print("log edit:", sent[-1][1]["embeds"][0]["fields"][:3])
    # /plan — Planner 비어 있음 → 안내
    sheets.log_event = lambda op: "ok"
    r, ms = await call(cmd("plan")); assert r["type"] == 5 and ms < 50
    e = sent[-1][1]["embeds"][0]; assert "no new rows" in e["description"] and not sent[-1][1]["components"]
    # /plan 한 줄 규칙: ADA #1 10/5~10/9 평일 9am-5pm
    r, _ = await call(cmd("plan", account="ada", days="Mon-Fri", time="9am-5pm", player=s0.player, **{"from": "10-05", "to": "10-09"}))
    e = sent[-1][1]["embeds"][0]; print("plan preview:", e["fields"][:5], e["description"][:200])
    btn = sent[-1][1]["components"][0]["components"][0]; assert btn["label"].startswith("Apply 5")
    r, _ = await call(comp(btn["custom_id"])); assert r["type"] == 7
    print("plan applied:", sent[-2][1]["content"], "|", sent[-1][1][:120] if isinstance(sent[-1][1], str) else sent[-1])
    rows = [x for x in book.tabs["Schedule"].data[1:] if x[3] == "ADA" and str(x[4]) == "1" and "2026-10-05" <= str(x[0]) <= "2026-10-09"]
    assert len(rows) == 5 and all(x[5] == "09:00-17:00" and x[6] == s0.player for x in rows)
    r, _ = await call(comp(btn["custom_id"])); assert "Already applied" in r["data"]["content"]
    # 자동완성: 그날 시프트가 이름 옆에
    r, ms = await call({"type": 4, "member": member, "data": {"name": "ot", "options": [
        {"name": "staff", "value": s0.player[:2], "focused": True}, {"name": "date", "value": "2026-09-24"}]}})
    print(f"autocomplete+ctx {ms:.1f}ms ->", [c["name"] for c in r["data"]["choices"]][:3])
    assert any("#" in c["name"] for c in r["data"]["choices"]) and r["data"]["choices"][0]["value"] == s0.player
    r, _ = await call({"type": 4, "member": member, "data": {"name": "off", "options": [{"name": "account", "value": "ad", "focused": True}]}})
    print("account ac:", r["data"]["choices"][0]["name"])
    # /schedule 인자 없이 → 오늘 전체 (빈 자리 먼저)
    r, ms = await call(cmd("schedule")); em = r["data"]["embeds"]
    total = sum(len(e["title"]) + len(e["description"]) for e in em)
    print(f"/schedule overview {ms:.1f}ms, {len(em)} embeds, {total} chars:", [e["title"] for e in em])
    assert total <= 6000 and em[1]["title"].startswith("Client") and em[2]["title"].startswith("Farming")
    body = "\n".join(e["description"] for e in em[1:3])
    assert "OFF" not in body and "no player" not in em[2]["description"], "OFF / unassigned farming hidden"
    print(em[1]["description"][:300])

    # 텔레그램 영업 요청 → 디스코드 카드 → 버튼
    from services import notify, telegram as tg, translate, parser
    posted, tgsent = [], []
    async def fake_post(ch, payload): posted.append((ch, payload)); return {"id": "m1"}
    async def fake_tg(chat, text): tgsent.append((chat, text))
    async def fake_tr(t): return "번역:" + t
    notify.post, tg.send, translate.en_to_kr = fake_post, fake_tg, fake_tr
    notify.BOT_TOKEN, notify.REQUESTS_CH, notify.URGENT_ROLE = "x", "C1", "R1"
    op = {"type": "EXTEND", "character": "ADA", "date": "2026-09-26", "shift_time": "09:00-17:00", "new_time": "09:00-19:00"}
    store["t1"] = {"flow": "schedule", "sales_chat_id": "222", "sales_name": "Kim Sales", "op": op,
                   "en": "ADA 9/26 extend to 19:00", "sheet_result": "ADA 9/26 → 09:00-19:00", "status": "await_manager"}
    await notify.send_request("t1", op, "ADA 9/26 extend to 19:00", "ADA 9/26 → 09:00-19:00", "Kim Sales")
    card = posted[-1][1]; btns = [b["custom_id"] for b in card["components"][0]["components"]]
    print("card:", card["embeds"][0]["title"], btns); assert btns == ["req_ok|t1", "req_reply|t1", "req_sched|t1"]
    r, _ = await call(comp("req_sched|t1")); print(r["data"]["content"][:200]); assert "ADA" in r["data"]["content"]
    r, _ = await call(comp("req_reply|t1")); assert r["type"] == 9
    modal = {"type": 5, "token": "tk3", "member": member, "message": {"id": "m1"},
             "data": {"custom_id": "req_modal|t1", "components": [{"type": 1, "components": [{"custom_id": "text", "value": "OK, player informed"}]}]}}
    r, _ = await call(modal); assert r["type"] == 7 and r["data"]["components"]      # 답장 후에도 Confirm 남음
    assert tgsent[-1][0] == "222" and "번역:OK" in tgsent[-1][1]
    r, _ = await call(comp("req_ok|t1")); assert r["type"] == 7 and not r["data"]["components"]
    print("closed card:", r["data"]["embeds"][0]["fields"][-1]); assert "확정 완료" in tgsent[-1][1]
    r, _ = await call(comp("req_ok|t1")); assert "Already handled" in r["data"]["content"]
    # 질문 → Answer 버튼만, 답하면 닫힘
    q = {"type": "QUESTION", "character": "Jjuni", "question": "버땅 vs 심연?"}
    store["t2"] = {"flow": "schedule", "sales_chat_id": "222", "op": q, "en": "Which gives more EXP?", "status": "await_answer"}
    await notify.send_request("t2", q, "Which gives more EXP?")
    assert [b["label"] for b in posted[-1][1]["components"][0]["components"]] == ["Answer"]
    r, _ = await call({**modal, "data": {**modal["data"], "custom_id": "req_modal|t2"}})
    assert not r["data"]["components"] and "매니저 답변" in tgsent[-1][1]
    # 긴급 → 역할 멘션
    await notify.send_request("t3", {"type": "RELOGIN", "character": "ADA"}, "relogin now")
    assert posted[-1][1]["content"] == "<@&R1>" and posted[-1][1]["allowed_mentions"]["roles"] == ["R1"]
    print("ALL DISCORD TESTS OK")
asyncio.run(main())

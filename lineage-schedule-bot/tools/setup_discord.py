"""새 디스코드 서버 자동 설정 — 역할·채널 만들기, 명령 등록, 안내문 고정, env.yaml 채우기.

  1) DISCORD_BOT_TOKEN=... python tools/setup_discord.py            → 봇 초대 링크 출력 (봇이 아직 서버에 없을 때)
  2) 초대 링크로 새 서버에 봇 추가 후 같은 명령 다시 실행            → 설정 + env.yaml 자동 기입
  3) 배포 후: ... python tools/setup_discord.py --endpoint https://<서비스URL>   → Interactions Endpoint 자동 등록

여러 번 실행해도 안전 (이미 있는 역할·채널은 그대로 사용). 토큰 값은 화면에 출력하지 않음.
"""
import os, sys, re, argparse, httpx
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SHEET_ID", "unused")
from services.discord_bot import COMMANDS  # noqa: E402

API = "https://discord.com/api/v10"
ENV = os.path.join(os.path.dirname(__file__), "..", "env.yaml")
ROLE = "Manager"
CATEGORY = "LINEAGE OPS"
CHANNELS = {   # 이름 → (용도, env 키)
    "sales-requests": ("Sales requests from Telegram — Confirm / Reply / Schedule buttons", "DISCORD_REQUESTS_CHANNEL_ID"),
    "manager-desk": ("Managers: /schedule /ot /incentive /penalty /assign /off /extend /plan /log", None),
    "bot-log": ("Everything the bot saved (OT, incentives, plans, schedule edits)", "DISCORD_LOG_CHANNEL_ID"),
    "schedule-confirm": ("Daily & weekly schedule checks — confirm players before the day/week starts", "DISCORD_CONFIRM_CHANNEL_ID"),
    "shift-reports": ("Players: /shot start & end screenshots → EXP % and Adena recorded", "DISCORD_REPORTS_CHANNEL_ID"),
}
# 봇 권한: 채널 보기·메시지·임베드·기록 읽기·메시지 관리·역할 멘션·메시지 고정(PIN_MESSAGES) + (설정용) 채널·역할 관리
PERMS = 1024 | 2048 | 16384 | 65536 | 8192 | 131072 | (1 << 51) | 16 | 268435456

GUIDE = """**How to use the bot** (type `/` and pick a command — names autocomplete with today's shifts)

🗓️ **Schedule**
`/schedule` today's board, gaps first · `/schedule account:ADA` · `/schedule staff:Reno`
`/assign account player [date] [slot] [time]` · `/off account [date] [slot]` · `/extend account new_time [date]`
`/plan` apply the **Planner** sheet tab (a month at once) · `/plan account:ADA days:Mon-Fri time:9am-5pm player:Cejay from:10-01 to:10-31`

💰 **Pay records**
`/ot staff hours [date] [time] [reason]` · `/incentive staff amount [date] [kpi]` · `/penalty staff hours [date] [action]`

✍️ `/log text` — write it like a message ("Reno 2h OT on Jjuni last night"), the bot fills the form.

✅ **Confirmations** (#schedule-confirm)
Every morning the bot posts today's shifts (morning first) → check players → **Confirm day**.
Every Saturday it creates next week (client players kept from last week) → fix gaps → **Confirm week**.
`/check kind:day` or `/check kind:week` posts a check now. Shifts over 8h are split into extra slots for another player.

Every command shows a preview first → **Confirm**. Only you see the preview. Saved records go to #bot-log.
Sales requests from Telegram arrive in #sales-requests → **✅ Confirm** / **💬 Reply** / **📋 Schedule**. The salesperson gets it in Korean."""


PLAYER_GUIDE = """**How to report your shift** (players)

1️⃣ Once: `/iam name:<your name>` — pick your name from the list.
2️⃣ At the **start** of your shift: `/shot` → attach a screenshot showing your **level + EXP %** and your **inventory with Adena**.
3️⃣ At the **end** of your shift: `/shot` again with the same kind of screenshot.

The bot reads Level / EXP % / Adena → check the numbers → **Confirm** (or **Fix numbers** if something is wrong).
At the end it shows what you gained this shift (EXP % and Adena). `/myshifts` shows your next 3 days."""


def pin_guide(token, channel, me, text, marker):
    recent = api("GET", f"/channels/{channel}/messages?limit=50", token, soft=True) or []
    mine = [m for m in recent if m["author"]["id"] == me and m.get("content", "").startswith(marker)]
    if mine:
        m = mine[0]
        api("PATCH", f"/channels/{channel}/messages/{m['id']}", token, json={"content": text})
    else:
        m = api("POST", f"/channels/{channel}/messages", token, json={"content": text})
    return bool(m.get("pinned") or api("PUT", f"/channels/{channel}/pins/{m['id']}", token, soft=True) is not None)


def api(method, path, token, soft=False, **kw):
    """soft=True: 실패해도 멈추지 않고 None (고정처럼 없어도 되는 작업)"""
    r = httpx.request(method, API + path, headers={"Authorization": f"Bot {token}"}, timeout=30, **kw)
    if r.status_code >= 300:
        if soft:
            return None
        sys.exit(f"❌ {method} {path} → {r.status_code} {r.text[:300]}")
    return r.json() if r.content else {}


def write_env(values: dict):
    if not os.path.exists(ENV):
        print("\n(env.yaml 이 없어 아래 값을 직접 넣어주세요)")
        for k, v in values.items():
            print(f'{k}: "{"<봇 토큰>" if k == "DISCORD_BOT_TOKEN" else v}"')
        return
    text = open(ENV, encoding="utf-8").read()
    for k, v in values.items():
        line = f'{k}: "{v}"'
        if re.search(rf"^{k}:.*$", text, flags=re.M):
            text = re.sub(rf"^{k}:.*$", line.replace("\\", "\\\\"), text, count=1, flags=re.M)
        else:
            text += ("" if text.endswith("\n") else "\n") + line + "\n"
    open(ENV, "w", encoding="utf-8").write(text)
    print("✅ env.yaml 에 기입: " + ", ".join(values))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--guild", help="서버 ID (봇이 서버 여러 개에 있을 때)")
    ap.add_argument("--endpoint", help="배포된 서비스 URL — Interactions Endpoint 로 등록")
    a = ap.parse_args()
    token = os.environ.get("DISCORD_BOT_TOKEN") or sys.exit("DISCORD_BOT_TOKEN 을 환경변수로 주세요")

    app = api("GET", "/applications/@me", token)
    app_id, public_key = app["id"], app["verify_key"]
    print(f"봇: {app['name']} (App ID {app_id})")

    guilds = api("GET", "/users/@me/guilds", token)
    if a.guild:
        guilds = [g for g in guilds if g["id"] == a.guild]
    if len(guilds) != 1:
        url = (f"https://discord.com/oauth2/authorize?client_id={app_id}"
               f"&scope=bot+applications.commands&permissions={PERMS}")
        if not guilds:
            print(f"\n① 이 링크로 새 서버에 봇을 추가한 뒤 다시 실행하세요:\n{url}")
        else:
            print("봇이 여러 서버에 있습니다. --guild <서버 ID> 로 지정하세요:",
                  ", ".join(f"{g['name']}={g['id']}" for g in guilds))
        return
    g = guilds[0]
    gid = g["id"]
    print(f"서버: {g['name']} ({gid})")

    roles = {r["name"]: r for r in api("GET", f"/guilds/{gid}/roles", token)}
    role = roles.get(ROLE) or api("POST", f"/guilds/{gid}/roles", token,
                                  json={"name": ROLE, "mentionable": True, "hoist": True, "color": 0x2ECC71})
    print(f"역할 '{ROLE}': {role['id']}  ← 매니저들에게 이 역할을 주세요 (서버 설정 > 멤버)")

    chans = api("GET", f"/guilds/{gid}/channels", token)
    cat = next((c for c in chans if c["type"] == 4 and c["name"].upper() == CATEGORY), None) \
        or api("POST", f"/guilds/{gid}/channels", token, json={"name": CATEGORY, "type": 4})
    ids = {}
    for name, (topic, _) in CHANNELS.items():
        ch = next((c for c in chans if c["type"] == 0 and c["name"] == name), None) \
            or api("POST", f"/guilds/{gid}/channels", token,
                   json={"name": name, "type": 0, "topic": topic, "parent_id": cat["id"]})
        ids[name] = ch["id"]
        print(f"#{name}: {ch['id']}")

    cmds = api("PUT", f"/applications/{app_id}/guilds/{gid}/commands", token, json=COMMANDS)
    print("명령 등록:", ", ".join("/" + c["name"] for c in cmds))

    me = api("GET", "/users/@me", token)["id"]
    for ch, text, marker in ((ids["manager-desk"], GUIDE, "**How to use the bot**"),
                             (ids["shift-reports"], PLAYER_GUIDE, "**How to report your shift**")):
        name = next(n for n, i in ids.items() if i == ch)
        print(f"사용법 안내 #{name} " + ("고정" if pin_guide(token, ch, me, text, marker)
                                        else "올림 — 고정 권한이 없어 직접 고정해주세요 (메시지 ⋯ > 메시지 고정)"))

    if a.endpoint:
        url = a.endpoint.rstrip("/") + "/discord/interactions"
        api("PATCH", "/applications/@me", token, json={"interactions_endpoint_url": url})
        print(f"Interactions Endpoint 등록: {url}")

    values = {"DISCORD_APP_ID": app_id, "DISCORD_PUBLIC_KEY": public_key, "DISCORD_BOT_TOKEN": token,
              "DISCORD_GUILD_ID": gid, "DISCORD_MANAGER_ROLE_IDS": role["id"], "DISCORD_URGENT_ROLE_ID": role["id"]}
    for name, (_, key) in CHANNELS.items():
        if key: values[key] = ids[name]
    write_env(values)
    if not a.endpoint:
        print("\n다음: ./deploy.sh 후  python tools/setup_discord.py --endpoint <서비스 URL>")


if __name__ == "__main__":
    main()

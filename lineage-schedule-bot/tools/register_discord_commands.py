"""디스코드 슬래시 명령 등록 (서버 단위라 즉시 반영). 명령을 바꿨을 때마다 한 번 실행.

  DISCORD_APP_ID=... DISCORD_BOT_TOKEN=... DISCORD_GUILD_ID=... python tools/register_discord_commands.py
"""
import os, sys, json, httpx
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SHEET_ID", "unused")
from services.discord_bot import COMMANDS  # noqa: E402

app, token, guild = (os.environ[k] for k in ("DISCORD_APP_ID", "DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID"))
r = httpx.put(f"https://discord.com/api/v10/applications/{app}/guilds/{guild}/commands",
              headers={"Authorization": f"Bot {token}"}, json=COMMANDS, timeout=30)
print(r.status_code, ", ".join(c["name"] for c in r.json()) if r.status_code < 300 else r.text)

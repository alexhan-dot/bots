"""디스코드 웹훅 알림/발송 (매니저 컨펌은 알림 속 링크 클릭)"""
import os, httpx

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
BOT_BASE_URL = os.environ.get("BOT_BASE_URL", "")

def format_manager_msg(op, en, sheet_result) -> str:
    return (f"📅 **Schedule Update**\n{en}\n\n"
            f"Sheet: {sheet_result}\n"
            f"✅ Confirm: {BOT_BASE_URL}/confirm?sid={{sid}}")

async def discord(text: str, buttons_sid: str | None = None):
    if not DISCORD_WEBHOOK:
        return
    if buttons_sid:
        text = text.replace("{sid}", buttons_sid)
    async with httpx.AsyncClient(timeout=30) as c:
        await c.post(DISCORD_WEBHOOK, json={"content": text[:1900]})

async def send_to_trainers(en: str, kr: str):
    await discord(f"📢 **Notice**\n{en}\n\n[원문/한글]\n{kr}")

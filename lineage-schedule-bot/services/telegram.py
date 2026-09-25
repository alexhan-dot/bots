"""Telegram Bot API 헬퍼"""
import os, httpx

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

async def send(chat_id: str, text: str):
    async with httpx.AsyncClient() as c:
        await c.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text})

async def send_buttons(chat_id: str, text: str, buttons: list[tuple[str, str]]):
    """buttons: [(label, callback_data)] — 2개씩 한 줄"""
    rows, row = [], []
    for label, data in buttons:
        row.append({"text": label, "callback_data": data[:64]})
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    async with httpx.AsyncClient() as c:
        await c.post(f"{API}/sendMessage", json={
            "chat_id": chat_id, "text": text,
            "reply_markup": {"inline_keyboard": rows}})

async def answer_callback(callback_id: str, text: str = ""):
    async with httpx.AsyncClient() as c:
        await c.post(f"{API}/answerCallbackQuery",
                     json={"callback_query_id": callback_id, "text": text})

async def get_file_url(file_id: str) -> str:
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{API}/getFile", json={"file_id": file_id})
        path = r.json()["result"]["file_path"]
        return f"https://api.telegram.org/file/bot{TOKEN}/{path}"

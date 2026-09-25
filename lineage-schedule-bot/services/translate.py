"""Claude API — 번역, 초안 작성, 수정 반영"""
import os, httpx, json
from services import glossary

API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

async def _claude(system: str, user: str) -> str:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01"},
            json={"model": MODEL, "max_tokens": 1500, "system": system,
                  "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip()

async def kr_to_en(text: str) -> str:
    return await _claude(
        "You translate Korean game-boosting operation messages to clear English for "
        "Filipino staff. Keep character names, times (HH:MM-HH:MM), and dates exactly "
        "as written. Use the glossary's English terms for game jargon. Output only the translation.\n\n"
        + glossary.prompt_block(), text)

async def draft_trainer_message(source: str) -> tuple[str, str]:
    out = await _claude(
        "You write instructions for game-boosting trainers based on a customer/sales "
        "message (Korean). Return JSON only: {\"kr\": \"한글 안내문\", \"en\": \"English version\"}. "
        "Be concise and action-oriented. Keep names/times verbatim. Use glossary English terms.\n\n"
        + glossary.prompt_block(), source)
    d = json.loads(out.replace("```json", "").replace("```", ""))
    return d["kr"], d["en"]

async def revise_draft(kr: str, en: str, instruction: str) -> tuple[str, str]:
    out = await _claude(
        "Revise the following KR/EN draft per the instruction. "
        "Return JSON only: {\"kr\": ..., \"en\": ...}.",
        f"[KR]\n{kr}\n[EN]\n{en}\n[수정 지시]\n{instruction}")
    d = json.loads(out.replace("```json", "").replace("```", ""))
    return d["kr"], d["en"]

async def en_to_kr(text: str) -> str:
    return await _claude("Translate English to natural Korean (존댓말). Keep game terms per glossary. Output only the translation.\n\n"
                         + glossary.prompt_block(), text)

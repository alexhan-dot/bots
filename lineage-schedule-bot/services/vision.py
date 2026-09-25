"""리니지 클래식 게임 스크린샷 → 레벨 · EXP % · 아데나 (Claude 비전, JSON 고정 출력).

숫자를 못 읽으면 -1 (레벨은 0) 로 돌려주고, 플레이어가 미리보기에서 [Fix] 로 고침.
"""
import os, json, base64, logging
import anthropic

log = logging.getLogger("vision")
MODEL = os.environ.get("DISCORD_AI_MODEL", "claude-opus-5")
_client = None
MAX_BYTES = 5 * 1024 * 1024                    # 이미지 한 장 상한 (API 제한)
TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

SYSTEM = """You read screenshots from the MMORPG Lineage Classic (NCSoft, Korean or English UI) for a
game-boosting team. Extract exactly what the screen shows:
- level: the character level (Lv / 레벨 / LV next to the name or the EXP bar). 0 if not visible.
- exp_percent: the experience percentage for the current level, as shown (e.g. 37.4512). Usually next to
  the level or on the EXP bar at the bottom. -1 if not visible.
- adena: the Adena (아데나) amount, normally in the inventory window (e.g. 1,234,567 → 1234567). -1 if not visible.
- character: the character name if clearly shown, else "".
Read digits carefully; do not confuse HP/MP, damage numbers or item counts with EXP or Adena.
Never guess a number that is not on screen."""

SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "integer"},
        "exp_percent": {"type": "number"},
        "adena": {"type": "integer"},
        "character": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string", "description": "anything unclear, short"},
    },
    "required": ["level", "exp_percent", "adena", "character", "confidence", "notes"],
    "additionalProperties": False,
}


def _anthropic():
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(timeout=45.0, max_retries=1)
    return _client


async def read_screenshot(data: bytes, media_type: str) -> dict:
    """반환: {level, exp_percent, adena, character, confidence, notes}. 실패 시 ValueError"""
    if media_type not in TYPES:
        raise ValueError(f"Please upload a PNG or JPG screenshot (got {media_type or 'unknown'}).")
    if len(data) > MAX_BYTES:
        raise ValueError("Image is larger than 5 MB — crop it or save as JPG.")
    resp = await _anthropic().beta.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.standard_b64encode(data).decode("utf-8")}},
            {"type": "text", "text": "Read level, EXP % and Adena from this screenshot."},
        ]}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        raise ValueError("The AI could not read this image. Use Fix to type the numbers.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text)

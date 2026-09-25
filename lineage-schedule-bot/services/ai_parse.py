"""/log 자유 입력(영어) → manager.build() 입력값. AI는 필드 추출만 하고, 스케줄 찾기·검증은 인덱스가 함.

속도: 짧은 시스템 프롬프트 + JSON 스키마 고정 출력 + effort low. 이름 매칭은 AI가 아니라 index 가 처리하므로
직원/계정 목록을 프롬프트에 넣지 않음 (입력 토큰 최소화).
"""
import os, json, logging
import anthropic
from services import clock
from services.manager import KINDS

log = logging.getLogger("ai_parse")
MODEL = os.environ.get("DISCORD_AI_MODEL", "claude-opus-5")
_client = anthropic.AsyncAnthropic(timeout=15.0, max_retries=1)

SYSTEM = """You turn a manager's short note about a game-boosting shift into one JSON record.
kind: ot (overtime), incentive (bonus/performance pay), penalty (death penalty / hours removed),
assign (put a player on a shift), off (cancel/stop a shift), extend (change a shift's time).
Copy names exactly as written. Dates: YYYY-MM-DD, or "today"/"yesterday"/"tomorrow" as written.
Times: HH:MM-HH:MM (24h; "12am" = 24:00 as a start, "8am" = 08:00). Leave unknown fields empty ("" or 0)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "staff": {"type": "string", "description": "employee the record is about (ot/incentive/penalty)"},
        "player": {"type": "string", "description": "player to assign (assign)"},
        "account": {"type": "string", "description": "game character / account name"},
        "date": {"type": "string"},
        "hours": {"type": "number"},
        "amount": {"type": "number"},
        "time": {"type": "string", "description": "OT time range, or the existing shift time"},
        "new_time": {"type": "string", "description": "new shift time (extend)"},
        "reason": {"type": "string"},
    },
    "required": ["kind", "staff", "player", "account", "date", "hours", "amount", "time", "new_time", "reason"],
    "additionalProperties": False,
}


async def parse(text: str) -> dict:
    """반환: {"kind": ..., 필드...} (빈 값 제거). 실패 시 ValueError"""
    resp = await _client.beta.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"Today is {clock.today().isoformat()}.\n\n{text}"}],
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",                 # 정책 거절 시 서버가 다른 모델로 자동 재시도
    )
    if resp.stop_reason == "refusal":
        raise ValueError("The AI declined this note. Please use the form commands instead.")
    text_out = next((b.text for b in resp.content if b.type == "text"), "")
    data = json.loads(text_out)
    return {k: v for k, v in data.items() if v not in ("", 0, None) or k == "kind"}

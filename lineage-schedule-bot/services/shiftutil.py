"""시프트 시간 계산 — 슬롯은 사람이 정하지 않고, 8시간을 넘는 요청만 8시간씩 나눠 추가 슬롯으로.

표기 규칙 (시트와 같음)
- "HH:MM-HH:MM", 끝이 시작보다 작으면 다음날에 끝남 ("20:00-04:00")
- "24:00-08:00" = 그 날짜 밤 자정에 시작
- 나눠진 조각이 자정 이후(24시 초과)에 시작하면 다음 날짜 행으로 ("20:00-10:00" → 20:00-04:00 + 다음날 04:00-10:00)
"""
import datetime, re

MAX_HOURS = 8
_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def span(time: str) -> tuple[int, int] | None:
    """분 단위 (시작, 끝). "20:00-04:00" → (1200, 1680), "24:00-08:00" → (1440, 1920)"""
    m = _RE.match(time or "")
    if not m:
        return None
    s = int(m[1]) * 60 + int(m[2])
    e = int(m[3]) * 60 + int(m[4])
    length = (e - s) % 1440 or (1440 if e != s else 0)
    return (s, s + length) if length else None


def hours(time: str) -> float:
    sp = span(time)
    return (sp[1] - sp[0]) / 60 if sp else 0.0


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def split(date: datetime.date, time: str, max_hours: int = MAX_HOURS) -> list[tuple[datetime.date, str]]:
    """8시간 이하면 그대로 [(date, time)], 넘으면 8시간씩 조각 [(date, time), ...]"""
    sp = span(time)
    if not sp or sp[1] - sp[0] <= max_hours * 60:
        return [(date, time)]
    out, s = [], sp[0]
    while s < sp[1]:
        e = min(s + max_hours * 60, sp[1])
        d, ss, ee = date, s, e
        if ss > 1440 or (ss == 1440 and out):          # 자정 넘어 시작하는 조각은 다음 날짜로
            d, ss, ee = date + datetime.timedelta(days=1), ss - 1440, ee - 1440
        end = ee if ee <= 1440 else ee - 1440
        out.append((d, f"{_fmt(ss)}-{_fmt(end)}"))
        s = e
    return out

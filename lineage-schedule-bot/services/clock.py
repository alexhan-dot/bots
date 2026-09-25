"""로컬 시간대 기준 날짜/시각 — Cloud Run 컨테이너는 UTC라서 date.today()를 그대로 쓰면 하루가 어긋남"""
import os, datetime
from zoneinfo import ZoneInfo

TZ_NAME = os.environ.get("BOT_TZ", "Asia/Seoul")
TZ = ZoneInfo(TZ_NAME)

def now() -> datetime.datetime:
    return datetime.datetime.now(TZ)

def today() -> datetime.date:
    return now().date()

def stamp() -> str:
    return now().strftime("%Y-%m-%dT%H:%M")

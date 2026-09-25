"""카카오 상담톡 — TalkBridge(해피톡) 개발자 모드 어댑터
수신: 웹훅 신호(kind/seq/userKey) → 서명 검증 → 본문 조회 API → 텍스트/첨부 처리
발신: POST /api/agent/send (컨펌 후 고객에게 접수/확정 회신)
문서: https://api.talkbridge.io/
"""
import os, hmac, hashlib, time, httpx
from urllib.parse import urlparse

BASE = "https://api.talkbridge.io"
AGENT_KEY = os.environ.get("TALKBRIDGE_AGENT_KEY", "")     # blumnb-...
BRAND_KEY = os.environ.get("TALKBRIDGE_BRAND_KEY", "")     # brand key
WHSEC = os.environ.get("TALKBRIDGE_WHSEC", "")             # whsec_...
_H = {"Authorization": f"Bearer {AGENT_KEY}", "Content-Type": "application/json"}

def verify_signature(raw_body: bytes, ts: str, sig: str, tolerance=300) -> bool:
    """X-Bridge-Signature = 'v0=' + hex(HMAC_SHA256(whsec, 'v0:'+ts+':'+raw_body))"""
    if not (WHSEC and ts and sig):
        return False
    try:
        if abs(time.time() - int(ts)) > tolerance:      # 리플레이 방지
            return False
    except ValueError:
        return False
    mac = hmac.new(WHSEC.encode(), b"v0:" + ts.encode() + b":" + raw_body, hashlib.sha256)
    return hmac.compare_digest(sig, "v0=" + mac.hexdigest())

async def fetch_latest_message(user_key: str) -> dict | None:
    """kind=message 신호 수신 후 본문 조회. 최신순 첫 message 항목 반환."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{BASE}/api/agent/rooms/{user_key}/messages",
                        params={"brand": BRAND_KEY, "max": 5}, headers=_H)
        r.raise_for_status()
        for m in r.json().get("messages", []):
            if m.get("kind") == "message":
                return m
    return None

def attachment_kind(text: str) -> str | None:
    """본문이 카카오 CDN URL이면 첨부. 확장자로 종류 판별."""
    try:
        u = urlparse(text.strip())
    except Exception:
        return None
    if u.scheme != "https" or u.netloc != "talk.kakaocdn.net":
        return None
    ext = u.path.rsplit(".", 1)[-1].lower()
    if ext in ("jpg", "jpeg", "png", "gif", "webp"): return "image"
    if ext in ("m4a", "mp3", "aac", "ogg", "wav"): return "voice"
    if ext in ("mp4", "mov"): return "video"
    return "file"

async def send_text(user_key: str, text: str) -> str | None:
    """고객(영업자)에게 회신. BrandWrite 키 필요. 반환: serial"""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/api/agent/send", headers=_H,
                         json={"brandKey": BRAND_KEY, "userKey": user_key, "text": text[:1000]})
        r.raise_for_status()
        return r.json().get("serial")

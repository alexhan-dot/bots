"""이미지(Vision) / 음성(STT) → 텍스트"""
import os, base64, httpx
from services import telegram as tg

API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

def kind_of(msg: dict) -> str:
    if "photo" in msg: return "image"
    if "voice" in msg or "audio" in msg: return "voice"
    return "document"

async def extract_text(msg: dict) -> str:
    if "photo" in msg:
        file_id = msg["photo"][-1]["file_id"]      # 최고 해상도
        url = await tg.get_file_url(file_id)
        return await _image_to_text(url)
    if "voice" in msg or "audio" in msg:
        f = msg.get("voice") or msg.get("audio")
        url = await tg.get_file_url(f["file_id"])
        return await _speech_to_text(url)
    if "document" in msg:
        url = await tg.get_file_url(msg["document"]["file_id"])
        mime = msg["document"].get("mime_type", "")
        if mime.startswith("image/"): return await _image_to_text(url, mime)
        if mime.startswith("audio/"): return await _speech_to_text(url)
    return "(지원하지 않는 형식)"

async def _image_to_text(url: str, media_type: str = "image/jpeg") -> str:
    async with httpx.AsyncClient(timeout=60) as c:
        img = (await c.get(url)).content
    b64 = base64.b64encode(img).decode()
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01"},
            json={"model": MODEL, "max_tokens": 1500, "messages": [{
                "role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": media_type, "data": b64}},
                    {"type": "text", "text":
                     "이 이미지의 내용을 읽어주세요. 카카오톡 대화 스크린샷이면 대화 내용을 "
                     "그대로 옮기고, 그 외 이미지면 핵심 내용을 한글로 설명하세요."}]}]})
        r.raise_for_status()
        return r.json()["content"][0]["text"]

async def _speech_to_text(url: str) -> str:
    """Google Cloud Speech-to-Text (ko-KR). 모든 포맷(ogg/m4a/mp3)을 ffmpeg로 FLAC 16k mono 변환."""
    import subprocess, tempfile
    from google.cloud import speech
    async with httpx.AsyncClient(timeout=60) as c:
        audio_bytes = (await c.get(url)).content
    with tempfile.NamedTemporaryFile(suffix=".in", delete=False) as f:
        f.write(audio_bytes); src = f.name
    dst = src + ".flac"
    subprocess.run(["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar", "16000", dst],
                   check=True, capture_output=True)
    client = speech.SpeechClient()
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.FLAC, sample_rate_hertz=16000,
        language_code="ko-KR", enable_automatic_punctuation=True)
    resp = client.recognize(config=config, audio=speech.RecognitionAudio(content=open(dst, "rb").read()))
    return " ".join(r.alternatives[0].transcript for r in resp.results) or "(음성 인식 실패)"

async def from_kakao_text(text: str) -> tuple[str, str]:
    """상담톡 본문(text) → (변환된 텍스트, 종류). 카카오 CDN URL이면 첨부 처리."""
    from services import kakao
    kind = kakao.attachment_kind(text)
    if kind == "image":
        ext = text.strip().rsplit(".", 1)[-1].lower()
        mt = {"png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")
        return await _image_to_text(text.strip(), mt), "image"
    if kind == "voice":
        return await _speech_to_text(text.strip()), "voice"
    if kind in ("video", "file"):
        return f"(첨부 {kind} 수신 — 자동 처리 불가) {text}", kind
    return text, "text"

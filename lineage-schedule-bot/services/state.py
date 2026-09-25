"""컨펌 대기 상태 저장 — Firestore"""
import uuid
from google.cloud import firestore

db = firestore.Client()
COL = "pending"

def create(**kwargs) -> str:
    sid = uuid.uuid4().hex[:10]
    db.collection(COL).document(sid).set(kwargs)
    return sid

def get(sid: str) -> dict | None:
    doc = db.collection(COL).document(sid).get()
    return doc.to_dict() if doc.exists else None

def update(sid: str, **kwargs):
    db.collection(COL).document(sid).update(kwargs)

def delete(sid: str):
    db.collection(COL).document(sid).delete()

def set_editor(sid: str, chat_id: str):
    update(sid, editor_chat_id=chat_id)

def clear_editor(sid: str):
    update(sid, editor_chat_id=None)

def get_pending_by_editor(chat_id: str):
    q = db.collection(COL).where("editor_chat_id", "==", chat_id).limit(1).stream()
    for doc in q:
        return doc.id, doc.to_dict()
    return None

def mark_seen(key: str) -> bool:
    """처음 보는 키면 True(기록), 이미 있으면 False. 웹훅 멱등 처리용."""
    ref = db.collection("seen").document(key.replace("/", "_"))
    if ref.get().exists:
        return False
    ref.set({"at": firestore.SERVER_TIMESTAMP})
    return True


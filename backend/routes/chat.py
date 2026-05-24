import json
import logging
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import ChatMessage, ChatState, User
from services.chatbot_engine import handle_chat

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatReq(BaseModel):
    query: str
    user_id: int


def _save(db: Session, user_id: int, role: str, text: str):
    db.add(ChatMessage(user_id=user_id, role=role, text=text, ts=time.time()))
    db.commit()


def _history(db: Session, user_id: int) -> list[dict]:
    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.ts)
    ).all()
    return [
        {"id": item.id, "user_id": item.user_id, "role": item.role, "text": item.text, "ts": item.ts}
        for item in messages
    ]


def _run_chat(db: Session, req: ChatReq) -> str:
    query = req.query.strip()
    user = db.get(User, req.user_id)
    if not user:
        return "User account was not found."

    history_before = _history(db, req.user_id)
    _save(db, req.user_id, "user", query)
    try:
        reply = handle_chat(query, user, db, history=history_before)
    except Exception:
        logger.exception("chat request failed user_id=%s", req.user_id)
        reply = "I could not process that message right now. Please try again."
    _save(db, req.user_id, "assistant", reply)
    return reply


@router.post("/chat")
def chat(req: ChatReq, db: Session = Depends(get_db)):
    return {"response": _run_chat(db, req)}


@router.post("/chat/stream")
def chat_stream(req: ChatReq, db: Session = Depends(get_db)):
    def events():
        reply = _run_chat(db, req)
        for word in reply.split(" "):
            yield f"data: {json.dumps({'delta': word + ' '}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/chat/history")
def history(user_id: int, db: Session = Depends(get_db)):
    return _history(db, user_id)


@router.delete("/chat/history")
def clear_history(user_id: int, db: Session = Depends(get_db)):
    db.execute(delete(ChatMessage).where(ChatMessage.user_id == user_id))
    db.execute(delete(ChatState).where(ChatState.user_id == user_id))
    db.commit()
    return {"ok": True}

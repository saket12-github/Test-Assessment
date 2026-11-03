from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from ..config import get_settings
from ..db import get_db
from ..models import Agent, Conversation, Customer, Draft, Message
from ..schemas import DraftOut, LoginRequest, MessageOut, ReplyRequest, iso
from ..security import SESSION_COOKIE, authenticate, create_session, current_agent, delete_session
from ..services import (
    ConflictError,
    NotFoundError,
    close_conversation,
    generate_draft,
    latest_active_draft,
    retry_draft,
    send_agent_reply,
)

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- auth


@router.post("/auth/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    agent = authenticate(db, body.email, body.password)
    if agent is None:
        raise HTTPException(401, "Incorrect email or password")
    token = create_session(db, agent)
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", secure=settings.cookie_secure,
        max_age=settings.session_ttl_hours * 3600,
    )
    return {"id": agent.id, "name": agent.name, "email": agent.email}


@router.post("/auth/logout")
def logout(response: Response, db: Session = Depends(get_db), token: str | None = Cookie(None, alias=SESSION_COOKIE)):
    if token:
        delete_session(db, token)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(agent: Agent = Depends(current_agent)):
    return {"id": agent.id, "name": agent.name, "email": agent.email}


# ---------------------------------------------------------------- conversations


def _draft_out(d: Draft | None) -> DraftOut | None:
    if d is None:
        return None
    return DraftOut(
        id=d.id, message_id=d.message_id, status=d.status, text=d.text, needs_human=d.needs_human,
        note=d.note, kb_topics=d.kb_topics or [], error=d.error, attempts=d.attempts,
        updated_at=iso(d.updated_at),
    )


@router.get("/conversations")
def list_conversations(
    status: str = "open", db: Session = Depends(get_db), _: Agent = Depends(current_agent)
):
    """Inbox: newest activity first, with a preview of the latest message."""
    if status not in ("open", "closed", "all"):
        raise HTTPException(422, "status must be open, closed or all")

    last_id = (
        select(func.max(Message.id)).where(Message.conversation_id == Conversation.id).scalar_subquery()
    )
    last = aliased(Message)
    active_draft = (
        select(Draft.status)
        .where(Draft.conversation_id == Conversation.id, Draft.status.in_(("pending", "ready", "failed")))
        .order_by(Draft.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    q = (
        select(Conversation, Customer, last, active_draft)
        .join(Customer, Customer.id == Conversation.customer_id)
        .join(last, last.id == last_id)
        .where(Conversation.brand_id == get_settings().brand_id)
        .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
        .limit(200)
    )
    if status != "all":
        q = q.where(Conversation.status == status)

    return [
        {
            "id": conv.id,
            "status": conv.status,
            "customer": {"name": cust.name, "phone": cust.phone},
            "last_message": {"text": msg.text[:140], "sender": msg.sender, "created_at": iso(msg.created_at)},
            "last_message_at": iso(conv.last_message_at),
            "awaiting_reply": msg.sender == "customer",
            "draft_status": draft_status,
        }
        for conv, cust, msg, draft_status in db.execute(q)
    ]


def _conversation_or_404(db: Session, conversation_id: int) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.brand_id != get_settings().brand_id:
        raise HTTPException(404, "Conversation not found")
    return conv


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db), _: Agent = Depends(current_agent)):
    conv = _conversation_or_404(db, conversation_id)
    messages = db.scalars(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at, Message.id)
    )
    return {
        "id": conv.id,
        "status": conv.status,
        "customer": {
            "name": conv.customer.name, "phone": conv.customer.phone, "external_id": conv.customer.external_id,
        },
        "messages": [
            MessageOut(
                id=m.id, sender=m.sender, text=m.text, created_at=iso(m.created_at),
                agent_name=m.agent.name if m.agent else None,
            )
            for m in messages
        ],
        "draft": _draft_out(latest_active_draft(db, conv.id)),
    }


def _map_errors(fn, *args):
    try:
        return fn(*args)
    except NotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ConflictError as exc:
        raise HTTPException(409, str(exc))


@router.post("/conversations/{conversation_id}/reply")
def reply(
    conversation_id: int, body: ReplyRequest, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)
):
    """Approve a (possibly edited) draft, or send a reply written from scratch (draft_id null)."""
    _conversation_or_404(db, conversation_id)
    msg = _map_errors(send_agent_reply, db, conversation_id, agent.id, body.text, body.draft_id)
    return MessageOut(id=msg.id, sender="agent", text=msg.text, created_at=iso(msg.created_at), agent_name=agent.name)


@router.post("/conversations/{conversation_id}/draft/regenerate")
def regenerate(
    conversation_id: int, background: BackgroundTasks, db: Session = Depends(get_db), _: Agent = Depends(current_agent)
):
    _conversation_or_404(db, conversation_id)
    draft = _map_errors(retry_draft, db, conversation_id)
    background.add_task(generate_draft, draft.id)
    return _draft_out(draft)


@router.post("/conversations/{conversation_id}/close")
def close(conversation_id: int, db: Session = Depends(get_db), _: Agent = Depends(current_agent)):
    _conversation_or_404(db, conversation_id)
    conv = _map_errors(close_conversation, db, conversation_id)
    return {"id": conv.id, "status": conv.status}

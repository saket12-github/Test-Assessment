"""Business logic shared by the webhook, the Customer view and the Agent view."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import SessionLocal
from .llm import ChatTurn, LLMError, get_llm
from .models import Conversation, Customer, Draft, Message, utcnow

log = logging.getLogger(__name__)

ACTIVE_DRAFT_STATUSES = ("pending", "ready", "failed")
# A draft still "pending" after this long was lost (e.g. the server restarted mid-call).
STUCK_AFTER = timedelta(seconds=90)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ConflictError(Exception):
    """The request is valid but clashes with the current state (HTTP 409)."""


class NotFoundError(Exception):
    pass


@dataclass
class InboundMessage:
    brand_id: str
    customer_external_id: str
    customer_name: str
    customer_phone: str
    text: str
    source: str  # webhook | customer_view
    event_id: str | None = None
    external_message_id: str | None = None
    sent_at: datetime | None = None


@dataclass
class IngestResult:
    duplicate: bool
    conversation_id: int
    message_id: int
    draft_id: int | None


# ---------------------------------------------------------------- ingestion


def ingest_customer_message(db: Session, msg: InboundMessage) -> IngestResult:
    """Store a customer message and queue an AI draft for it.

    Idempotent on event_id: a redelivered webhook returns the original message.
    Safe under concurrent deliveries: unique constraints decide, and we retry on a clash.
    """
    for attempt in range(3):
        if msg.event_id:
            existing = db.scalars(select(Message).where(Message.event_id == msg.event_id)).first()
            if existing:
                return IngestResult(True, existing.conversation_id, existing.id, None)
        try:
            return _ingest_once(db, msg)
        except IntegrityError:
            # Another request created the same customer / open conversation / event first.
            db.rollback()
            log.info("Ingest race on attempt %d for customer %s, retrying", attempt + 1, msg.customer_external_id)
    raise ConflictError("Could not store message due to concurrent updates; please retry")


def _ingest_once(db: Session, msg: InboundMessage) -> IngestResult:
    now = utcnow()
    customer = db.scalars(
        select(Customer).where(
            Customer.brand_id == msg.brand_id, Customer.external_id == msg.customer_external_id
        )
    ).first()
    if customer is None:
        customer = Customer(
            brand_id=msg.brand_id,
            external_id=msg.customer_external_id,
            name=msg.customer_name,
            phone=msg.customer_phone,
        )
        db.add(customer)
        db.flush()
    elif (customer.name, customer.phone) != (msg.customer_name, msg.customer_phone):
        customer.name, customer.phone = msg.customer_name, msg.customer_phone

    conversation = db.scalars(
        select(Conversation).where(
            Conversation.customer_id == customer.id, Conversation.status == "open"
        )
    ).first()
    if conversation is None:
        conversation = Conversation(brand_id=msg.brand_id, customer_id=customer.id, status="open", created_at=now)
        db.add(conversation)
        db.flush()

    message = Message(
        conversation_id=conversation.id,
        sender="customer",
        text=msg.text,
        event_id=msg.event_id,
        external_message_id=msg.external_message_id,
        source=msg.source,
        sent_at=msg.sent_at,
        created_at=now,
    )
    db.add(message)
    conversation.last_message_at = now
    db.flush()

    # Only the newest customer message gets a live draft; older suggestions are now stale.
    db.execute(
        update(Draft)
        .where(Draft.conversation_id == conversation.id, Draft.status.in_(ACTIVE_DRAFT_STATUSES))
        .values(status="superseded", updated_at=now)
    )
    draft = Draft(conversation_id=conversation.id, message_id=message.id, status="pending")
    db.add(draft)
    db.commit()
    log.info(
        "Stored message %s (conversation %s, source %s, event %s)",
        message.id, conversation.id, msg.source, msg.event_id,
    )
    return IngestResult(False, conversation.id, message.id, draft.id)


# ---------------------------------------------------------------- drafts


def generate_draft(draft_id: int) -> None:
    """Ask the LLM for a draft. Runs in the background with its own DB session.

    Any failure is recorded on the draft (status=failed, error=...) so the agent sees it
    and can retry, instead of the message silently having no suggestion.
    """
    db = SessionLocal()
    try:
        draft = db.get(Draft, draft_id)
        if draft is None or draft.status != "pending":
            return
        draft.attempts += 1
        db.commit()

        message = draft.message
        conversation = db.get(Conversation, draft.conversation_id)
        history = [
            ChatTurn(m.sender, m.text)
            for m in db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id, Message.id <= message.id)
                .order_by(Message.id)
            )
        ]
        try:
            result = get_llm().generate_draft(conversation.customer.name, history)
            values = dict(
                status="ready", text=result.reply, needs_human=result.needs_human,
                note=result.note or None, kb_topics=result.topics, model=result.model, error=None,
            )
            log.info("Draft %s ready (needs_human=%s)", draft_id, result.needs_human)
        except LLMError as exc:
            values = dict(status="failed", error=str(exc))
            log.error("Draft %s failed: %s", draft_id, exc)
        except Exception as exc:  # never let a background task die silently
            values = dict(status="failed", error=f"Unexpected error: {type(exc).__name__}")
            log.exception("Draft %s crashed", draft_id)

        # Only write if still pending: a newer message may have superseded it meanwhile.
        db.execute(
            update(Draft)
            .where(Draft.id == draft_id, Draft.status == "pending")
            .values(updated_at=utcnow(), **values)
        )
        db.commit()
    finally:
        db.close()


def retry_draft(db: Session, conversation_id: int) -> Draft:
    draft = latest_active_draft(db, conversation_id)
    if draft is None:
        raise NotFoundError("No draft to regenerate for this conversation")
    if draft.status == "pending" and _as_utc(draft.updated_at) > utcnow() - STUCK_AFTER:
        raise ConflictError("A draft is already being generated")
    draft.status, draft.error, draft.updated_at = "pending", None, utcnow()
    db.commit()
    return draft


def latest_active_draft(db: Session, conversation_id: int) -> Draft | None:
    return db.scalars(
        select(Draft)
        .where(Draft.conversation_id == conversation_id, Draft.status.in_(ACTIVE_DRAFT_STATUSES))
        .order_by(Draft.id.desc())
    ).first()


def pending_draft_ids(db: Session) -> list[int]:
    return list(db.scalars(select(Draft.id).where(Draft.status == "pending")))


# ---------------------------------------------------------------- agent actions


def send_agent_reply(
    db: Session, conversation_id: int, agent_id: int, text: str, draft_id: int | None
) -> Message:
    """Save an agent's reply. If it came from a draft, mark that draft as used.

    The draft row is locked so two agents cannot approve the same draft twice.
    """
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise NotFoundError("Conversation not found")
    if conversation.status != "open":
        raise ConflictError("This conversation is closed")

    now = utcnow()
    draft = None
    if draft_id is not None:
        draft = db.scalars(select(Draft).where(Draft.id == draft_id).with_for_update()).first()
        if draft is None or draft.conversation_id != conversation_id:
            raise NotFoundError("Draft not found")
        if draft.status == "used":
            raise ConflictError("This draft was already approved")
        if draft.status == "superseded":
            raise ConflictError("The customer sent a newer message. Review the latest draft before replying.")

    reply = Message(
        conversation_id=conversation_id, sender="agent", text=text, source="agent",
        agent_id=agent_id, created_at=now,
    )
    db.add(reply)
    db.flush()
    conversation.last_message_at = now

    if draft is not None:
        draft.status, draft.used_at, draft.reply_message_id = "used", now, reply.id
    # Any other open suggestion is now answered, so retire it.
    db.execute(
        update(Draft)
        .where(Draft.conversation_id == conversation_id, Draft.status.in_(ACTIVE_DRAFT_STATUSES))
        .values(status="superseded", updated_at=now)
    )
    db.commit()
    return reply


def close_conversation(db: Session, conversation_id: int) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise NotFoundError("Conversation not found")
    if conversation.status == "open":
        conversation.status = "closed"
        conversation.closed_at = datetime.now(timezone.utc)
        db.execute(
            update(Draft)
            .where(Draft.conversation_id == conversation_id, Draft.status.in_(ACTIVE_DRAFT_STATUSES))
            .values(status="superseded")
        )
        db.commit()
    return conversation

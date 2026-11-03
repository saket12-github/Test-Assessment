import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..schemas import WebhookPayload
from ..security import verify_signature
from ..services import InboundMessage, generate_draft, ingest_customer_message

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhook")
async def receive_webhook(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Receive a customer message from the chat platform.

    Order matters: verify the signature on the *raw* bytes before parsing anything.
    Events we deliberately skip get a 2xx so the sender does not keep retrying them.
    """
    settings = get_settings()
    raw = await request.body()

    if not verify_signature(raw, request.headers.get("X-Signature"), settings.webhook_secret):
        log.warning("Webhook rejected: bad or missing signature")
        raise HTTPException(401, "Invalid signature")

    try:
        payload = WebhookPayload.model_validate(json.loads(raw))
    except json.JSONDecodeError:
        raise HTTPException(400, "Body is not valid JSON")
    except ValidationError as exc:
        log.warning("Webhook rejected: invalid payload: %s", exc.errors(include_url=False))
        raise HTTPException(422, exc.errors(include_url=False, include_context=False))

    if payload.brand_id != settings.brand_id:
        raise HTTPException(422, f"Unknown brand_id '{payload.brand_id}'")

    if payload.type != "message.received":
        log.info("Ignoring webhook event %s of type %s", payload.event_id, payload.type)
        return {"status": "ignored", "reason": f"event type '{payload.type}' is not handled"}

    if payload.message.type != "text":
        log.info("Ignoring %s message in event %s", payload.message.type, payload.event_id)
        return {"status": "ignored", "reason": f"message type '{payload.message.type}' is not supported"}

    text = (payload.message.text or "").strip()
    if not text:
        raise HTTPException(422, "message.text must not be empty")

    # DB work is synchronous; keep it off the event loop.
    result = await run_in_threadpool(
        ingest_customer_message,
        db,
        InboundMessage(
            brand_id=payload.brand_id,
            customer_external_id=payload.customer.id,
            customer_name=payload.customer.name,
            customer_phone=payload.customer.phone,
            text=text,
            source="webhook",
            event_id=payload.event_id,
            external_message_id=payload.message.id,
            sent_at=payload.timestamp,
        ),
    )
    if result.duplicate:
        return {"status": "duplicate", "conversation_id": result.conversation_id, "message_id": result.message_id}

    background.add_task(generate_draft, result.draft_id)
    return {
        "status": "accepted",
        "conversation_id": result.conversation_id,
        "message_id": result.message_id,
        "draft_id": result.draft_id,
    }

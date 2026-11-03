"""Customer view: a test stand-in for the real chat app. No sign-in, by design (see README)."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Conversation, Customer, Message
from ..schemas import CustomerSendRequest, MessageOut, iso
from ..services import InboundMessage, generate_draft, ingest_customer_message

router = APIRouter(prefix="/api/customer")

TEST_CUSTOMERS = [
    {"id": "cust_1042", "name": "Aisha Khan", "phone": "+919800000001"},
    {"id": "cust_1043", "name": "Rohan Mehta", "phone": "+919800000002"},
    {"id": "cust_1044", "name": "Priya Nair", "phone": "+919800000003"},
]


def customer_external_id(identity) -> str:
    # Typed-in customers have no platform id, so their phone number identifies them.
    return identity.id or f"phone_{identity.phone.lstrip('+')}"


@router.get("/test-customers")
def test_customers():
    return TEST_CUSTOMERS


@router.post("/messages")
def send_message(body: CustomerSendRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Exactly the same path as the webhook: same ingest function, same draft job."""
    settings = get_settings()
    result = ingest_customer_message(
        db,
        InboundMessage(
            brand_id=settings.brand_id,
            customer_external_id=customer_external_id(body.customer),
            customer_name=body.customer.name,
            customer_phone=body.customer.phone,
            text=body.text,
            source="customer_view",
            event_id=f"ui_{uuid.uuid4().hex}",
        ),
    )
    background.add_task(generate_draft, result.draft_id)
    return {"status": "accepted", "conversation_id": result.conversation_id, "message_id": result.message_id}


@router.get("/messages", response_model=list[MessageOut])
def list_messages(external_id: str = Query(min_length=1, max_length=128), db: Session = Depends(get_db)):
    """The customer's messages and approved agent replies. Drafts are never included."""
    customer = db.scalars(
        select(Customer).where(Customer.brand_id == get_settings().brand_id, Customer.external_id == external_id)
    ).first()
    if customer is None:
        return []
    messages = db.scalars(
        select(Message)
        .join(Conversation)
        .where(Conversation.customer_id == customer.id)
        .order_by(Message.created_at, Message.id)
    )
    return [
        MessageOut(id=m.id, sender=m.sender, text=m.text, created_at=iso(m.created_at))
        for m in messages
    ]

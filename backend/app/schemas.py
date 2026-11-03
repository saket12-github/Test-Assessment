import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TEXT = 4096
PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{6,19}$")


def _clean_text(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("must not be empty")
    return v


def _clean_phone(v: str) -> str:
    v = v.strip()
    if not PHONE_RE.match(v):
        raise ValueError("must be a phone number like +919800000001")
    return re.sub(r"[ \-]", "", v)


# ------------------------------------------------------------- webhook payload


class WebhookCustomer(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    phone: str

    _phone = field_validator("phone")(_clean_phone)
    _name = field_validator("name")(_clean_text)


class WebhookMessage(BaseModel):
    id: str | None = Field(default=None, max_length=128)
    type: str = "text"
    text: str | None = Field(default=None, max_length=MAX_TEXT)


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str = Field(min_length=1, max_length=128)
    type: str
    brand_id: str
    timestamp: datetime | None = None
    customer: WebhookCustomer
    message: WebhookMessage


# ------------------------------------------------------------- customer view


class CustomerIdentity(BaseModel):
    # Optional: test customers send their fixed id; a typed-in customer is keyed by phone.
    id: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    phone: str

    _phone = field_validator("phone")(_clean_phone)
    _name = field_validator("name")(_clean_text)


class CustomerSendRequest(BaseModel):
    customer: CustomerIdentity
    text: str = Field(min_length=1, max_length=MAX_TEXT)

    _text = field_validator("text")(_clean_text)


# ------------------------------------------------------------- agent


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class ReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    draft_id: int | None = None

    _text = field_validator("text")(_clean_text)


# ------------------------------------------------------------- responses


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite drops tzinfo; everything we store is UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MessageOut(BaseModel):
    id: int
    sender: Literal["customer", "agent"]
    text: str
    created_at: str
    agent_name: str | None = None


class DraftOut(BaseModel):
    id: int
    message_id: int
    status: str
    text: str | None
    needs_human: bool
    note: str | None
    kb_topics: list[str]
    error: str | None
    attempts: int
    updated_at: str


class CustomerOut(BaseModel):
    id: int
    external_id: str
    name: str
    phone: str

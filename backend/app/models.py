"""Database schema.

customers 1─* conversations 1─* messages
                         messages(customer) 1─* drafts   (AI suggestions for that message)
agents 1─* agent_sessions
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    # We store only a SHA-256 of the session token, never the token itself.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    agent: Mapped[Agent] = relationship()


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[str] = mapped_column(String(64))
    # The id the chat platform uses for this customer (e.g. "cust_1042").
    external_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("uq_customer_brand_external", "brand_id", "external_id", unique=True),)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[str] = mapped_column(String(64))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Denormalised so the inbox can sort without scanning messages.
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship()
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", order_by="Message.id"
    )

    __table_args__ = (
        Index("ix_conversation_brand_last", "brand_id", "last_message_at"),
        # Enforces "each customer has exactly one open conversation" in the database itself.
        Index(
            "uq_one_open_conversation",
            "customer_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
            sqlite_where=text("status = 'open'"),
        ),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender: Mapped[str] = mapped_column(String(16))  # customer | agent
    text: Mapped[str] = mapped_column(Text)
    # Webhook event id, used to ignore redelivered events. Null for agent replies.
    event_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    external_message_id: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16))  # webhook | customer_view | agent
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"))
    # When the platform says the customer sent it (may differ from when we received it).
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    agent: Mapped[Agent | None] = relationship()


class Draft(Base):
    """An AI-suggested reply to one customer message. Never sent automatically."""

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    # pending -> ready | failed ; ready -> used ; any non-used -> superseded
    status: Mapped[str] = mapped_column(String(16), default="pending")
    text: Mapped[str | None] = mapped_column(Text)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)  # model's reason for flagging, shown to agent
    kb_topics: Mapped[list | None] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The agent reply this draft became (possibly edited).
    reply_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))

    message: Mapped[Message] = relationship(foreign_keys=[message_id])

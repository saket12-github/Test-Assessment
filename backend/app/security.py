import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Agent, AgentSession

SESSION_COOKIE = "verdant_session"


def verify_signature(raw_body: bytes, header_value: str | None, secret: str) -> bool:
    """Check an `X-Signature: sha256=<hex>` header against HMAC-SHA256(raw body)."""
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value[len("sha256="):].strip().lower())


def sign(raw_body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# Used to keep login timing similar whether or not the email exists.
_DUMMY_HASH = hash_password("timing-equaliser")


def authenticate(db: Session, email: str, password: str) -> Agent | None:
    agent = db.query(Agent).filter(Agent.email == email.strip().lower()).one_or_none()
    if agent is None:
        check_password(password, _DUMMY_HASH)
        return None
    return agent if check_password(password, agent.password_hash) else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, agent: Agent) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.add(
        AgentSession(
            token_hash=_token_hash(token),
            agent_id=agent.id,
            created_at=now,
            expires_at=now + timedelta(hours=get_settings().session_ttl_hours),
        )
    )
    # Opportunistic cleanup of expired sessions.
    db.query(AgentSession).filter(AgentSession.expires_at < now).delete()
    db.commit()
    return token


def delete_session(db: Session, token: str) -> None:
    db.query(AgentSession).filter(AgentSession.token_hash == _token_hash(token)).delete()
    db.commit()


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def current_agent(
    db: Session = Depends(get_db),
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> Agent:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    if not token:
        raise unauthorized
    session = db.get(AgentSession, _token_hash(token))
    if session is None or _as_utc(session.expires_at) < datetime.now(timezone.utc):
        raise unauthorized
    return session.agent

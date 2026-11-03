import json
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["AGENT_SEED_EMAIL"] = "agent@verdant.test"
os.environ["AGENT_SEED_PASSWORD"] = "correct-horse"
os.environ["TOGETHER_API_KEY"] = "test-key"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import llm  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.llm import DraftResult, LLMError  # noqa: E402
from app.main import app, seed_agent  # noqa: E402
from app.security import sign  # noqa: E402

SECRET = "verdant-demo-secret-2026"


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.fail = False

    def generate_draft(self, customer_name, history):
        self.calls.append((customer_name, history))
        if self.fail:
            raise LLMError("provider down")
        return DraftResult(reply=f"Hi {customer_name.split()[0]}, thanks for reaching out.", topics=["Shipping"], model="fake")


@pytest.fixture
def fake_llm():
    fake = FakeLLM()
    llm.set_llm(fake)
    return fake


@pytest.fixture
def client(fake_llm):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        seed_agent()
        yield c


@pytest.fixture
def agent_client(client):
    r = client.post("/api/auth/login", json={"email": "agent@verdant.test", "password": "correct-horse"})
    assert r.status_code == 200
    return client


def payload(event_id="evt_1", text="Hi, my order #4471 hasn't arrived yet. Can I change the delivery address?",
            customer_id="cust_1042", name="Aisha Khan", **overrides):
    body = {
        "event_id": event_id,
        "type": "message.received",
        "brand_id": "verdant-co",
        "timestamp": "2026-11-03T10:15:22Z",
        "customer": {"id": customer_id, "name": name, "phone": "+919800000001"},
        "message": {"id": f"msg_{event_id}", "type": "text", "text": text},
    }
    body.update(overrides)
    return body


def post_webhook(client, body, secret=SECRET, signature=None):
    raw = json.dumps(body).encode()
    sig = signature if signature is not None else sign(raw, secret)
    return client.post("/webhook", content=raw, headers={"Content-Type": "application/json", "X-Signature": sig})

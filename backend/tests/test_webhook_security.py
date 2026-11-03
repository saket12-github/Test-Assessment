from .conftest import payload, post_webhook


def test_rejects_missing_or_bad_signature(client):
    assert post_webhook(client, payload(), signature="").status_code == 401
    assert post_webhook(client, payload(), secret="wrong-secret").status_code == 401
    assert post_webhook(client, payload(), signature="sha256=deadbeef").status_code == 401


def test_signature_covers_raw_body(client):
    import json
    from app.security import sign
    raw = json.dumps(payload()).encode()
    sig = sign(raw, "verdant-demo-secret-2026")
    tampered = raw.replace(b"4471", b"9999")
    r = client.post("/webhook", content=tampered, headers={"X-Signature": sig})
    assert r.status_code == 401


def test_duplicate_event_is_idempotent(client, fake_llm):
    r1 = post_webhook(client, payload(event_id="evt_dup"))
    r2 = post_webhook(client, payload(event_id="evt_dup"))
    assert r1.status_code == r2.status_code == 200
    assert r2.json()["status"] == "duplicate"
    assert r2.json()["message_id"] == r1.json()["message_id"]
    assert len(fake_llm.calls) == 1


def test_invalid_payloads(client):
    assert post_webhook(client, {"hello": "world"}).status_code == 422
    assert post_webhook(client, payload(brand_id="other-brand")).status_code == 422
    body = payload()
    body["message"]["text"] = "   "
    assert post_webhook(client, body).status_code == 422
    body = payload()
    body["customer"]["phone"] = "not a phone"
    assert post_webhook(client, body).status_code == 422

    from app.security import sign
    raw = b"{not json"
    r = client.post("/webhook", content=raw, headers={"X-Signature": sign(raw, "verdant-demo-secret-2026")})
    assert r.status_code == 400


def test_unsupported_events_are_acknowledged(client, fake_llm):
    r = post_webhook(client, payload(type="message.read"))
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    body = payload(event_id="img")
    body["message"] = {"id": "m", "type": "image"}
    r = post_webhook(client, body)
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert fake_llm.calls == []


def test_agent_endpoints_require_sign_in(client):
    assert client.get("/api/conversations").status_code == 401
    assert client.get("/api/conversations/1").status_code == 401
    assert client.post("/api/conversations/1/reply", json={"text": "x"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "agent@verdant.test", "password": "nope"})
    assert r.status_code == 401


def test_logout_invalidates_session(agent_client):
    assert agent_client.get("/api/auth/me").status_code == 200
    agent_client.post("/api/auth/logout")
    assert agent_client.get("/api/conversations").status_code == 401

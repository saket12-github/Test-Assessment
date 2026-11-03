from .conftest import payload, post_webhook


def test_webhook_end_to_end(agent_client, fake_llm):
    r = post_webhook(agent_client, payload())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"
    conv_id = r.json()["conversation_id"]

    inbox = agent_client.get("/api/conversations").json()
    assert inbox[0]["customer"]["name"] == "Aisha Khan"
    assert inbox[0]["awaiting_reply"] is True
    assert inbox[0]["last_message"]["text"].startswith("Hi, my order #4471")

    conv = agent_client.get(f"/api/conversations/{conv_id}").json()
    draft = conv["draft"]
    assert draft["status"] == "ready"
    assert draft["text"] == "Hi Aisha, thanks for reaching out."
    assert draft["message_id"] == conv["messages"][0]["id"]

    r = agent_client.post(f"/api/conversations/{conv_id}/reply", json={"text": "Edited reply", "draft_id": draft["id"]})
    assert r.status_code == 200, r.text

    conv = agent_client.get(f"/api/conversations/{conv_id}").json()
    assert [m["sender"] for m in conv["messages"]] == ["customer", "agent"]
    assert conv["messages"][1]["text"] == "Edited reply"
    assert conv["draft"] is None  # used

    # Same draft cannot be approved twice.
    r = agent_client.post(f"/api/conversations/{conv_id}/reply", json={"text": "again", "draft_id": draft["id"]})
    assert r.status_code == 409

    # Customer sees the approved reply, never a draft.
    msgs = agent_client.get("/api/customer/messages", params={"external_id": "cust_1042"}).json()
    assert [m["text"] for m in msgs][-1] == "Edited reply"


def test_customer_view_uses_same_pipeline(agent_client, fake_llm):
    r = agent_client.post("/api/customer/messages", json={
        "customer": {"id": "cust_1042", "name": "Aisha Khan", "phone": "+919800000001"},
        "text": "What is your return policy?",
    })
    assert r.status_code == 200
    # A follow-up via webhook lands in the same open conversation.
    r2 = post_webhook(agent_client, payload(event_id="evt_2"))
    assert r2.json()["conversation_id"] == r.json()["conversation_id"]
    assert len(fake_llm.calls) == 2
    # Second draft saw both messages as history.
    assert len(fake_llm.calls[1][1]) == 2

    conv = agent_client.get(f"/api/conversations/{r.json()['conversation_id']}").json()
    assert conv["draft"]["message_id"] == conv["messages"][-1]["id"]


def test_customer_messages_never_include_drafts(client):
    client.post("/api/customer/messages", json={
        "customer": {"name": "New Person", "phone": "+91 98000 11111"}, "text": "Do you deliver to Dubai?",
    })
    msgs = client.get("/api/customer/messages", params={"external_id": "phone_919800011111"}).json()
    assert len(msgs) == 1 and msgs[0]["sender"] == "customer"
    assert set(msgs[0]) == {"id", "sender", "text", "created_at", "agent_name"}


def test_new_message_supersedes_old_draft(agent_client):
    first = post_webhook(agent_client, payload(event_id="evt_a")).json()
    conv_id = first["conversation_id"]
    old_draft = agent_client.get(f"/api/conversations/{conv_id}").json()["draft"]
    post_webhook(agent_client, payload(event_id="evt_b", text="Hello? Any update?"))

    r = agent_client.post(f"/api/conversations/{conv_id}/reply", json={"text": "x", "draft_id": old_draft["id"]})
    assert r.status_code == 409
    assert "newer message" in r.json()["detail"]


def test_llm_failure_is_visible_and_retryable(agent_client, fake_llm):
    fake_llm.fail = True
    conv_id = post_webhook(agent_client, payload()).json()["conversation_id"]
    draft = agent_client.get(f"/api/conversations/{conv_id}").json()["draft"]
    assert draft["status"] == "failed"
    assert "provider down" in draft["error"]

    fake_llm.fail = False
    r = agent_client.post(f"/api/conversations/{conv_id}/draft/regenerate")
    assert r.status_code == 200
    draft = agent_client.get(f"/api/conversations/{conv_id}").json()["draft"]
    assert draft["status"] == "ready" and draft["attempts"] == 2

    # The agent can also reply without any draft.
    r = agent_client.post(f"/api/conversations/{conv_id}/reply", json={"text": "Manual reply"})
    assert r.status_code == 200


def test_inbox_sorted_by_latest_activity(agent_client):
    post_webhook(agent_client, payload(event_id="e1", customer_id="c1", name="First"))
    post_webhook(agent_client, payload(event_id="e2", customer_id="c2", name="Second"))
    names = [c["customer"]["name"] for c in agent_client.get("/api/conversations").json()]
    assert names == ["Second", "First"]
    post_webhook(agent_client, payload(event_id="e3", customer_id="c1", name="First"))
    names = [c["customer"]["name"] for c in agent_client.get("/api/conversations").json()]
    assert names == ["First", "Second"]


def test_closed_conversation_starts_new_one(agent_client):
    conv_id = post_webhook(agent_client, payload(event_id="e1")).json()["conversation_id"]
    assert agent_client.post(f"/api/conversations/{conv_id}/close").json()["status"] == "closed"
    new_id = post_webhook(agent_client, payload(event_id="e2")).json()["conversation_id"]
    assert new_id != conv_id
    # Customer still sees full history across conversations.
    assert len(agent_client.get("/api/customer/messages", params={"external_id": "cust_1042"}).json()) == 2

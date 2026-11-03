#!/usr/bin/env python3
"""Send a signed test message to the webhook (standard library only).

    python scripts/send_webhook.py "What is your return policy?"
    python scripts/send_webhook.py --url https://<your-app>/webhook --customer-id cust_2001 --name "Ravi" --phone +919800000009 "Hi"
"""
import argparse
import hashlib
import hmac
import json
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

p = argparse.ArgumentParser()
p.add_argument("text")
p.add_argument("--url", default="http://localhost:8000/webhook")
p.add_argument("--secret", default="verdant-demo-secret-2026")
p.add_argument("--customer-id", default="cust_1042")
p.add_argument("--name", default="Aisha Khan")
p.add_argument("--phone", default="+919800000001")
p.add_argument("--event-id", default=None, help="reuse an id to test duplicate delivery")
a = p.parse_args()

body = json.dumps({
    "event_id": a.event_id or f"evt_{uuid.uuid4().hex[:8]}",
    "type": "message.received",
    "brand_id": "verdant-co",
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "customer": {"id": a.customer_id, "name": a.name, "phone": a.phone},
    "message": {"id": f"msg_{uuid.uuid4().hex[:8]}", "type": "text", "text": a.text},
}).encode()
sig = "sha256=" + hmac.new(a.secret.encode(), body, hashlib.sha256).hexdigest()
req = urllib.request.Request(a.url, data=body, method="POST",
                             headers={"Content-Type": "application/json", "X-Signature": sig})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(r.status, r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())

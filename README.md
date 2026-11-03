# Verdant & Co: AI-assisted support inbox

Customer messages arrive by signed webhook (or from the built-in Customer view), are stored in Postgres, and get an AI-drafted reply grounded in the brand's policies. A signed-in agent reviews, edits and approves the draft. Nothing reaches the customer without an agent's approval.

- **Live app:** `<DEPLOYED_URL>`. The toggle at the top switches between the Customer and Agent views.
- **Webhook:** `POST <DEPLOYED_URL>/webhook`, with an `X-Signature: sha256=<hmac>` header.
- **Agent sign-in:** `<AGENT_EMAIL>` / `<AGENT_PASSWORD>`.
- **Send a test webhook:** `python scripts/send_webhook.py --url <DEPLOYED_URL>/webhook "Do you deliver to Dubai?"`

## Run locally

```bash
cp .env.example .env              # add TOGETHER_API_KEY and an agent password
docker compose up --build         # Postgres + app at http://localhost:8000
```

Without Docker (Python 3.10+, Node 18+; uses a SQLite file unless `DATABASE_URL` is set):

```bash
python -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
(cd frontend && npm ci && npm run build)
cd backend && ../.venv/bin/uvicorn app.main:app --reload    # http://localhost:8000
../.venv/bin/python -m pytest                                # 21 tests; same suite passes on Postgres via TEST_DATABASE_URL
```

For frontend hot reload, run `npm run dev` in `frontend/` (it proxies `/api` to port 8000). `python -m app.create_agent email "Name"` adds an agent. To deploy, use `render.yaml` (a Render Blueprint: Docker web service plus Postgres).

## Main decisions

- **Stack.** I used FastAPI, SQLAlchemy and PostgreSQL, with React (Vite) served by the same process. One deployable on one origin keeps the session as a plain httpOnly cookie, with no CORS and no token handling in JavaScript. FastAPI validates the webhook payload with Pydantic.
- **Data model.** The tables are `customers` → `conversations` → `messages` (sender is `customer` or `agent`), `drafts`, `agents` and `agent_sessions`. Each draft is tied to the customer message it answers and moves through `pending → ready | failed → used | superseded`. A partial unique index enforces *one open conversation per customer* in the database, and `messages.event_id` is unique, so redelivered webhooks are ignored. All three ingestion races (customer, conversation, event) are settled by these constraints, with a retry on conflict. I tested this with 10 concurrent webhooks.
- **The webhook stays fast and failure-tolerant.** It checks the HMAC on the raw bytes first, stores the message, returns 200, and generates the draft in a background task. If the LLM fails, the draft is marked `failed` with the reason, and the agent sees it with **Retry** and can still reply by hand. Drafts left `pending` by a restart are resumed on startup. Event types and message types the app doesn't handle are acknowledged with 2xx and ignored, so the sender doesn't retry them forever.
- **The Customer view uses the same path as the webhook.** It calls the same `ingest_customer_message` and draft job, so its messages are handled exactly like webhook messages.
- **Concurrent agents.** Approving locks the draft row (`SELECT … FOR UPDATE`). A draft can be used only once, and approving a draft that a newer customer message has superseded returns 409, so an agent can't answer a stale question. The editor never overwrites what an agent has typed; it offers to load the newer draft instead.
- **AI and retrieval.** The model is Llama 3.3 70B via Together.ai, at temperature 0.2. The whole knowledge base (about 200 tokens) goes into the system prompt along with the last 12 messages. With so little text, a retrieval step has nothing to do and can only miss things. The model returns JSON `{reply, topics, needs_human, note}`. When a question isn't covered (Dubai), touches on health (an allergy), or asks for an order's status (which the bot cannot see), the rules tell it to say so honestly, promise a follow-up, and set `needs_human`. The agent then sees a ⚠ banner with the reason. Customer text is treated as data, not instructions. This approach stops working at roughly hundreds of policies, or when policies change often: then I'd chunk them, embed them in pgvector, retrieve the top results, and cite policy IDs.

**Left out because of the time limit:** Alembic migrations (tables are created at startup), a durable job queue (background tasks assume a single process), agent management UI, login rate limiting, pagination, WebSockets (the UI polls every 3–4 s), and authentication on the Customer view (it is a test tool by design; it reads by customer id).

## Where AI helped, and where it got something wrong

I built this with Claude Code: it scaffolded the models, routes, React views and tests, and wrote the Postgres concurrency and failure-injection checks. It got something wrong in the first version of the webhook handler: an `async def` route called the synchronous SQLAlchemy session directly, which would block the event loop and stall every other request whenever the database was slow. Reviewing the code caught it, and the database work now runs in a threadpool. It also first let the "regenerate" endpoint re-queue a draft that was still generating, which would have produced duplicate LLM calls. That now returns 409 unless the draft has been stuck for more than 90 seconds.

## Two questions

**1. A message arrives at 2 a.m. and something fails.** The customer's message is still stored, because it is saved before the AI call. In the morning the agent sees the conversation at the top of the inbox with a red **Draft failed** badge and the reason (for example "LLM provider returned HTTP 503"), with a Retry button and a box for writing the reply by hand. If saving itself failed, the webhook returned 5xx, so the platform retries, and the `event_id` makes the retry safe. To investigate, search the logs for the `event_id` or draft id (every step logs them), then check the `drafts.error` and `attempts` columns. `/health` shows whether the database and LLM are configured.

**2. The app now has to serve 20 brands.** First I'd make tenancy explicit. `brand_id` is already on customers and conversations, so I'd add a `brands` table holding each brand's webhook secret and knowledge base, link agents to brands, and scope every query by the signed-in agent's brand. The webhook would pick the brand's secret from the payload's `brand_id` (or a per-brand URL) before trusting the body. Moving the knowledge base from code into the database, editable per brand, comes next, because it is the part that differs most between brands. A real job queue would follow, since 20 brands means much more LLM traffic.

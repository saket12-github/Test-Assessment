import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .config import get_settings
from .db import SessionLocal, init_db
from .models import Agent
from .routes import agent, customer, webhook
from .security import hash_password
from .services import ConflictError, NotFoundError, generate_draft, pending_draft_ids

settings = get_settings()
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("verdant")

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def seed_agent() -> None:
    if not (settings.agent_seed_email and settings.agent_seed_password):
        return
    email = settings.agent_seed_email.strip().lower()
    with SessionLocal() as db:
        if db.query(Agent).filter(Agent.email == email).first() is None:
            db.add(Agent(email=email, name=settings.agent_seed_name, password_hash=hash_password(settings.agent_seed_password)))
            db.commit()
            log.info("Seeded agent %s", email)


def resume_pending_drafts() -> None:
    """Drafts left 'pending' by a restart or crash would otherwise never be generated."""
    with SessionLocal() as db:
        ids = pending_draft_ids(db)
    if ids:
        log.info("Resuming %d pending draft(s): %s", len(ids), ids)
    for draft_id in ids:
        generate_draft(draft_id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    seed_agent()
    if not settings.together_api_key:
        log.warning("TOGETHER_API_KEY is not set: drafts will fail until it is configured")
    threading.Thread(target=resume_pending_drafts, daemon=True).start()
    yield


app = FastAPI(title="Verdant & Co Support", lifespan=lifespan)
app.include_router(webhook.router)
app.include_router(customer.router)
app.include_router(agent.router)


@app.exception_handler(ConflictError)
async def _conflict(_: Request, exc: ConflictError):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(NotFoundError)
async def _not_found(_: Request, exc: NotFoundError):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    # Returning 500 makes the chat platform retry the webhook; the event_id makes that safe.
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


@app.get("/health")
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "llm_configured": bool(settings.together_api_key)}


# ---- React build (single deployable: API + UI on one origin, so the session cookie just works)
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "webhook")):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        file = FRONTEND_DIST / path
        if path and file.is_file() and FRONTEND_DIST in file.resolve().parents:
            return FileResponse(file)
        return FileResponse(FRONTEND_DIST / "index.html")

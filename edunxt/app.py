"""FastAPI app: Meta webhook endpoints plus the background worker."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response

from .agents.intake import IntakeAgent
from .agents.llm import make_client
from .agents.drafting import DraftingAgent
from .agents.research import ResearchAgent
from .channels.whatsapp import WhatsAppChannel, parse_webhook, verify_signature
from .config import Settings, load_settings
from .store import Store
from .workflow import Worker, Workflow

log = logging.getLogger(__name__)


def build_drafter(settings: Settings, client, universities_csv=None) -> DraftingAgent:
    return DraftingAgent(
        client, settings.draft_model, universities_csv or settings.partners_csv, settings.scholarships_csv, settings.draft_effort
    )


def build_researcher(settings: Settings, client, universities_csv=None, scholarships_csv=None, log_path=None) -> ResearchAgent:
    return ResearchAgent(
        client,
        settings.research_model,
        universities_csv or settings.partners_csv,
        scholarships_csv or settings.scholarships_csv,
        effort=settings.research_effort,
        max_searches=settings.research_max_searches,
        max_fetches=settings.research_max_fetches,
        fetch_max_tokens=settings.research_fetch_max_tokens,
        allowed_domains=settings.research_allowed_domains,
        log_path=log_path or settings.research_log,
    )


def build_workflow(settings: Settings, channel) -> Workflow:
    client = make_client(settings.anthropic_api_key)
    return Workflow(
        settings=settings,
        store=Store(settings.database_url),
        channel=channel,
        intake=IntakeAgent(client, settings.intake_model),
        drafter=build_drafter(settings, client),
        researcher=build_researcher(settings, client),
    )


def create_app(settings: Settings | None = None, workflow: Workflow | None = None, start_worker: bool = True) -> FastAPI:
    settings = settings or load_settings()
    if workflow is None:
        if not settings.whatsapp_configured:
            raise RuntimeError("WA_PHONE_NUMBER_ID and WA_ACCESS_TOKEN must be set to serve webhooks (see docs/SETUP.md)")
        channel = WhatsAppChannel(settings.wa_phone_number_id, settings.wa_access_token, settings.wa_api_version)
        workflow = build_workflow(settings, channel)
    worker = Worker(workflow)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="EduNXT agent", lifespan=lifespan)
    app.state.workflow = workflow

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/webhooks/meta")
    def verify(
        mode: str = Query("", alias="hub.mode"),
        token: str = Query("", alias="hub.verify_token"),
        challenge: str = Query("", alias="hub.challenge"),
    ):
        if mode == "subscribe" and settings.webhook_verify_token and token == settings.webhook_verify_token:
            return Response(content=challenge, media_type="text/plain")
        raise HTTPException(status_code=403, detail="verification failed")

    @app.post("/webhooks/meta")
    async def inbound(request: Request):
        body = await request.body()
        if not verify_signature(settings.meta_app_secret, body, request.headers.get("x-hub-signature-256")):
            raise HTTPException(status_code=401, detail="bad signature")
        for msg in parse_webhook(await request.json()):
            if workflow.receive(msg):
                log.info("queued message %s from %s", msg.message_id, msg.phone)
        return {"ok": True}  # always 200 fast; work happens in the worker

    return app

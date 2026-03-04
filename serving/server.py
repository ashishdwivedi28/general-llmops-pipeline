"""FastAPI server — ADK agent serving with health checks and feedback endpoint.

Integrates:
- ADK's ``get_fast_api_app()`` for agent serving (background-initialized via lifespan)
- Health / readiness endpoints for Cloud Run (always available)
- Feedback endpoint for user ratings
- OpenTelemetry observability

Design notes:
    Cloud Run startup probes need /health to respond within seconds.
    All heavy GCP/ADK initialization is launched as a background asyncio Task
    inside the lifespan handler so the server starts accepting health-check
    traffic immediately.  The /ready endpoint signals 503 until the agent is
    fully initialized.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from serving.utils.config import ServerConfig
from serving.utils.observability import setup_observability

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global startup state — populated by the background initializer task.
# All values default to None / False so the app stays runnable while the task
# is still starting.
# ---------------------------------------------------------------------------
_state: dict[str, Any] = {
    "ready": False,
    "error": None,
    "config": None,
    "agent": None,
    "interaction_logger": None,
    "guardrail_checker": None,
}


def _sync_initialize(app: FastAPI) -> None:  # pragma: no cover
    """Heavy (potentially blocking) GCP/ADK initialization.

    Runs inside a thread pool via ``asyncio.to_thread`` so it never blocks
    the event loop.  The /health endpoint remains responsive throughout.
    """
    config = ServerConfig()
    _state["config"] = config

    # -- Observability (non‑fatal) -------------------------------------------
    try:
        setup_observability(
            project_id=config.GCP_PROJECT_ID,
            enable_tracing=config.ENABLE_TRACING,
        )
    except Exception as exc:
        logger.warning("Observability setup failed: %s", exc)

    # -- Callbacks (non‑fatal) ------------------------------------------------
    try:
        from serving.callbacks import GuardrailChecker, InteractionLogger

        _state["interaction_logger"] = InteractionLogger(project_id=config.GCP_PROJECT_ID)
        _state["guardrail_checker"] = GuardrailChecker(
            valid_topics=config.VALID_TOPICS,
            invalid_topics=config.INVALID_TOPICS,
        )
    except Exception as exc:
        logger.warning("Callbacks init failed: %s", exc)

    # -- ADK Agent (non‑fatal — server runs in degraded mode on failure) ------
    try:
        from google.adk.cli.fast_api import get_fast_api_app

        from serving.agent import create_agent

        agent = create_agent(config)
        adk_app = get_fast_api_app(
            agent=agent,
            project=config.GCP_PROJECT_ID,
            location=config.GCP_LOCATION,
        )
        # Starlette/FastAPI allows dynamic mount — router lookup is runtime.
        app.mount("/agent", adk_app)
        _state["agent"] = agent
        _state["ready"] = True
        logger.info("Agent initialized — server is fully ready.")
    except Exception as exc:
        logger.error("Agent initialization failed: %s", exc)
        _state["error"] = str(exc)
        _state["ready"] = False


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: RUF029
    """Launch background init task and yield immediately.

    The background task calls the blocking GCP/ADK setup in a thread so
    the event loop stays free and /health responds at once.
    """
    asyncio.create_task(asyncio.to_thread(_sync_initialize, app))
    yield
    logger.info("Server shutting down.")
    _state.clear()


def create_app() -> FastAPI:
    """Create the FastAPI application with deferred ADK agent mounting."""
    app = FastAPI(
        title="LLMOps RAG Agent API",
        description="Production RAG agent powered by Google ADK + Vertex AI",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        """Health check — always 200 so Cloud Run startup probe passes immediately."""
        cfg = _state.get("config") or ServerConfig()
        return {"status": "healthy", "agent": cfg.AGENT_NAME}

    @app.get("/ready")
    async def ready():
        """Readiness — 503 until ADK agent is fully initialized."""
        if _state.get("ready"):
            return {"status": "ready"}
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "error": _state.get("error")},
        )

    @app.post("/feedback")
    async def feedback(request: Request):
        """Receive user feedback for an interaction."""
        body = await request.json()
        ilogger = _state.get("interaction_logger")
        if ilogger:
            ilogger.log_feedback(
                session_id=body.get("session_id", ""),
                interaction_id=body.get("interaction_id", ""),
                rating=body.get("rating", 0),
                comment=body.get("comment", ""),
            )
        return {"status": "recorded"}

    @app.post("/chat")
    async def chat(request: Request):
        """Direct chat endpoint (non-ADK) with guardrails and logging."""
        agent = _state.get("agent")
        if agent is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"error": "Agent not ready. Try again shortly."},
            )

        cfg = _state.get("config") or ServerConfig()
        gc = _state.get("guardrail_checker")
        ilogger = _state.get("interaction_logger")

        body = await request.json()
        query = body.get("query", "")
        session_id = body.get("session_id", "anonymous")

        # Input guardrail
        if gc:
            allowed, reason = gc.check_input(query)
            if not allowed:
                return JSONResponse(
                    status_code=400,
                    content={"error": reason, "response": "I can't help with that topic."},
                )

        start_time = time.time()

        try:
            from google.adk.runners import InMemoryRunner
            from google.genai import types

            runner = InMemoryRunner(agent=agent)
            session = await runner.session_service.create_session(
                app_name=cfg.AGENT_NAME,
                user_id=session_id,
            )
            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=query)],
            )

            response_text = ""
            async for event in runner.run_async(
                user_id=session_id,
                session_id=session.id,
                new_message=content,
            ):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            response_text += part.text

        except Exception as e:
            logger.error("Agent error: %s", e)
            response_text = "I'm sorry, an error occurred. Please try again."

        latency_ms = (time.time() - start_time) * 1000

        # Output guardrail
        if gc:
            output_ok, _ = gc.check_output(response_text)
            if not output_ok:
                response_text = "I generated a response but it was filtered for safety."

        # Log interaction
        if ilogger:
            ilogger.log_interaction(
                session_id=session_id,
                user_query=query,
                agent_response=response_text,
                latency_ms=latency_ms,
            )

        return {
            "response": response_text,
            "session_id": session_id,
            "latency_ms": round(latency_ms, 2),
        }

    return app


# Module-level app (for uvicorn / Cloud Run)
app = create_app()


if __name__ == "__main__":
    _cfg = ServerConfig()
    uvicorn.run(
        "serving.server:app",
        host=_cfg.HOST,
        port=_cfg.PORT,
        log_level=_cfg.LOG_LEVEL,
        reload=False,
    )

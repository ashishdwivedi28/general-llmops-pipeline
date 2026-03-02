"""FastAPI server — ADK agent serving with health checks and feedback endpoint.

Integrates:
- ADK's ``get_fast_api_app()`` for agent serving
- Health / readiness endpoints for Cloud Run
- Feedback endpoint for user ratings
- OpenTelemetry observability
"""

from __future__ import annotations

import logging
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.adk.cli.fast_api import get_fast_api_app

from serving.agent import create_agent
from serving.callbacks import GuardrailChecker, InteractionLogger
from serving.utils.config import ServerConfig
from serving.utils.observability import setup_observability

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create the FastAPI application with ADK agent mounting."""
    config = ServerConfig()

    # Setup observability
    setup_observability(
        project_id=config.GCP_PROJECT_ID,
        enable_tracing=config.ENABLE_TRACING,
    )

    # Create ADK agent
    agent = create_agent(config)

    # Get ADK FastAPI app
    adk_app = get_fast_api_app(
        agent=agent,
        project=config.GCP_PROJECT_ID,
        location=config.GCP_LOCATION,
    )

    # Create wrapper app with additional endpoints
    app = FastAPI(
        title="LLMOps RAG Agent API",
        description="Production RAG agent powered by Google ADK + Vertex AI",
        version="0.1.0",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount ADK app
    app.mount("/agent", adk_app)

    # Initialize services
    interaction_logger = InteractionLogger(project_id=config.GCP_PROJECT_ID)
    guardrail_checker = GuardrailChecker(
        valid_topics=config.VALID_TOPICS,
        invalid_topics=config.INVALID_TOPICS,
    )

    @app.get("/health")
    async def health():
        """Health check for Cloud Run."""
        return {"status": "healthy", "agent": config.AGENT_NAME}

    @app.get("/ready")
    async def ready():
        """Readiness check for Cloud Run."""
        return {"status": "ready"}

    @app.post("/feedback")
    async def feedback(request: Request):
        """Receive user feedback for an interaction."""
        body = await request.json()
        interaction_logger.log_feedback(
            session_id=body.get("session_id", ""),
            interaction_id=body.get("interaction_id", ""),
            rating=body.get("rating", 0),
            comment=body.get("comment", ""),
        )
        return {"status": "recorded"}

    @app.post("/chat")
    async def chat(request: Request):
        """Direct chat endpoint (non-ADK) with guardrails and logging.

        For simple integrations that don't need full ADK session management.
        """
        body = await request.json()
        query = body.get("query", "")
        session_id = body.get("session_id", "anonymous")

        # Input guardrail
        allowed, reason = guardrail_checker.check_input(query)
        if not allowed:
            return JSONResponse(
                status_code=400,
                content={"error": reason, "response": "I can't help with that topic."},
            )

        start_time = time.time()

        # Use ADK agent for response (simplified — full ADK uses sessions)
        try:
            from google.adk.runners import InMemoryRunner
            from google.genai import types

            runner = InMemoryRunner(agent=agent)
            session = await runner.session_service.create_session(
                app_name=config.AGENT_NAME,
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
        output_ok, output_reason = guardrail_checker.check_output(response_text)
        if not output_ok:
            response_text = "I generated a response but it was filtered for safety."

        # Log interaction
        interaction_logger.log_interaction(
            session_id=session_id,
            user_query=query,
            agent_response=response_text,
            latency_ms=latency_ms,
        )

        return {"response": response_text, "session_id": session_id, "latency_ms": round(latency_ms, 2)}

    return app


# Module-level app (for uvicorn / Cloud Run)
app = create_app()


if __name__ == "__main__":
    config = ServerConfig()
    uvicorn.run(
        "serving.server:app",
        host=config.HOST,
        port=config.PORT,
        log_level=config.LOG_LEVEL,
        reload=False,
    )

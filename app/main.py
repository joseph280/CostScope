"""
CostScope FastAPI application.

The HTTP entry point. Deliberately thin: this module knows about
HTTP, request validation, and error mapping — nothing else. All the
real work lives in app/orchestrator.py and the wrappers.

Endpoints:
  GET  /health   — liveness check (no LLM/DB calls)
  POST /chat     — main Q&A endpoint
  GET  /         — minimal landing page with curl examples

Production hardening that's intentionally not here for v1:
  - Auth (would be a FastAPI dependency)
  - Rate limiting (slowapi or upstream gateway)
  - CORS (uvicorn proxy / nginx)
  - Request IDs and distributed tracing
  - Caching of repeated questions
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.orchestrator import Orchestrator


# Logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Lifespan: build the orchestrator once at startup, reuse for every request

@asynccontextmanager 
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan hook. Instantiates the orchestrator once at startup
    so we don't pay the prompt-loading and wrapper-init cost on every
    request. Stores it on app.state for handler access.
    """
    logger.info("startup_begin model=%s", settings.anthropic_model)
    app.state.orchestrator = Orchestrator()
    logger.info("startup_ok")
    yield
    logger.info("shutdown")


app = FastAPI(
    title="CostScope",
    description="Natural language interface to enterprice cost data.",
    version="0.1.0",
    lifespan=lifespan,
)


# Request/response schemas
class ChatRequest(BaseModel):
    question: str = Field(
        ..., 
        min_length=1,
        max_length=1000,
        description="Natural-language question about the cost data.",
        examples=["What was total marketing spend in Q4 2025?"],
    )


class ChatResponse(BaseModel):
    question: str
    answer: str
    used_data: bool
    sql: str | None = None
    row_count: int | None = None
    cost_usd: float
    latency_ms: int
    claude_calls: int

# Endpoints
@app.get("/health")
def health() -> dict:
    """Liveness check. No upstream calls."""
    return {"status": "ok", "service": "CostScope", "version": "0.1.0"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """
    Main Q&A endpoint. Delegates the entire flow to the orchestrator.
    """
    logger.info("chat_request question=%r", req.question)
    try:
        result = app.state.orchestrator.answer(req.question)
    except Exception as e:
        logger.exception("chat_failed question=%r error=%s", req.question, str(e))
        # Catch-all: orchestrator already logs the specifics. We map to a
        # 500 here so the API contract stays clean — clients don't see
        # SDK-specific exception types.
        raise HTTPException(status_code=500, detail=f"Internal server error: {type(e).__name__}",
        )
    
    return ChatResponse(
        question=result.question,
        answer=result.answer,
        used_data=result.used_data,
        sql=result.sql,
        row_count=result.row_count,
        cost_usd=round(result.total_cost_usd, 6),
        latency_ms=result.total_latency_ms,
        claude_calls=result.claude_calls,
    )


@app.get("/", response_class=HTMLResponse)
def landing() -> str:
    """Tiny landing page so hitting the root in a browser isn't a 404."""
    return """
    <!doctype html>
    <html><head><title>CostScope</title>
    <style>
      body { font-family: -apple-system, sans-serif; max-width: 720px;
             margin: 4em auto; padding: 0 1em; color: #222; }
      h1 { color: #1a4f8b; }
      code { background: #f0f0f0; padding: 2px 6px; border-radius: 4px; }
      pre  { background: #f0f0f0; padding: 1em; border-radius: 6px; overflow-x: auto; }
    </style></head><body>
    <h1>CostScope</h1>
    <p>Natural-language interface to enterprise cost data.</p>
    <p>POST a question to <code>/chat</code>:</p>
    <pre>curl -s -X POST http://localhost:8000/chat \\
  -H "Content-Type: application/json" \\
  -d '{"question": "What was total marketing spend in Q4 2025?"}' | jq</pre>
    <p>Interactive API docs at <a href="/docs">/docs</a>.</p>
    </body></html>
    """
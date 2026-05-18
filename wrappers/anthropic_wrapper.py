"""
Anthropic Claude wrapper for CostScope.

Design intent
-------------
Production AI applications need more than a raw SDK call. This wrapper adds the
five things every Claude integration in a real codebase needs:

  1. Retry with exponential backoff — networks fail, rate limits happen
  2. Structured logging — observability for debugging and audit
  3. Token & cost tracking — finance teams will ask "what does this cost?"
  4. Latency tracking — useful for SLA conversations and UX work
  5. A single, typed entry point — keeps call sites clean

Configuration (API key, model, retry policy, pricing table) is intentionally
external — see app/config.py. The wrapper accepts overrides for testability
but defaults to the application config singleton.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from anthropic import Anthropic, APIError, APIConnectionError, RateLimitError

from app.config import ANTHROPIC_PRICING_PER_MTOK, settings

logger = logging.getLogger(__name__)

# Result types
@dataclass
class ClaudeResponse:
    text: str
    model: str
    input_tokens: str
    output_tokens: str
    cost_usd: float
    latency_ms: int
    attempts: int = 1

    def __str__(self) -> str:
        return (
            f"<ClaudeResponse model={self.model} "
            f"tokens={self.input_tokens}/{self.output_tokens} "
            f"cost=${self.cost_usd:.5f} "
            f"latency={self.latency_ms}ms attempts={self.attempts}>"
        )

@dataclass
class SessionStats:
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: int = 0
    
    def record(self, r: ClaudeResponse) -> None:
        self.total_calls += 1
        self.total_input_tokens += r.input_tokens
        self.total_output_tokens += r.output_tokens
        self.total_cost_usd += r.cost_usd
        self.total_latency_ms += r.latency_ms

# Module level singleton for the dev session. In the future this would be a
# Prometheus counter.
stats = SessionStats()

# --- Wrapper ---
class AnthropicWrapper:
    """Thin, typed wrapper around the Anthropic SDK with retry + observability."""

    def __init__(
            self,
            api_key: Optional[str] = None,
            model: Optional[str] = None,
            max_retries: Optional[int] = None,
            base_backoff_seconds: Optional[float] = None,
            request_timeout_seconds: Optional[float] = None
    ):
        # Constructor args win over config for tests and ad-hoc overrides.
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.anthropic_model
        self.max_retries = max_retries if max_retries is not None else settings.anthropic_max_retries
        self.base_backoff_seconds = base_backoff_seconds if base_backoff_seconds is not None else settings.anthropic_base_backoff_seconds
        timeout = request_timeout_seconds if request_timeout_seconds is not None else settings.anthropic_request_timeout_seconds
        if not self.api_key:
            raise ValueError("anthropic_api_key not set. Add ANTHROPIC_API_KEY to .env.")
        
        self.client = Anthropic(api_key=self.api_key, timeout=timeout)

    # Public API

    def complete(
            self,
            user: str,
            system: Optional[str] = None,
            max_tokens: int = 1024,
            temperature: float = 0.2,
    ) -> ClaudeResponse:
        """Send one user message and return a structured response."""
        messages = [{"role": "user", "content": user}]
        return self._call_with_retry(
            system = system,
            messages = messages,
            max_tokens = max_tokens,
            temperature = temperature,
            )
    

    # Internal; cost calculation

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        prices = ANTHROPIC_PRICING_PER_MTOK.get(self.model)
        if not prices:
            logger.warning(f"No pricing info for model {self.model}. Defaulting to $0 cost.")
            return 0.0
        input_cost = (input_tokens / 1_000_000) * prices["input"]
        output_cost = (output_tokens / 1_000_000) * prices["output"]
        return input_cost + output_cost

    # Internal; retry loop

    def _call_with_retry(
            self,
            system: Optional[str],
            messages: list[dict],
            max_tokens: int,
            temperature: float,
    ) -> ClaudeResponse:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            start = time.perf_counter()
            try:
                kwargs = {
                   "model": self.model,
                   "messages": messages,
                   "max_tokens": max_tokens,
                   "temperature": temperature,
                }
                if system:
                   kwargs["system"] = system
                resp = self.client.messages.create(**kwargs)
                latency_ms = int((time.perf_counter() - start) * 1000)

                text = resp.content[0].text if resp.content else ""
                input_tokens = resp.usage.input_tokens
                output_tokens = resp.usage.output_tokens
                cost_usd = self._estimate_cost(input_tokens, output_tokens)

                result = ClaudeResponse(
                    text=text,
                    model=self.model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    attempts=attempt,
                )
                stats.record(result)
                logger.info(f"Claude call success: {result}")
                return result
            
            except (RateLimitError, APIConnectionError) as e:
                last_error = e
                wait = self.base_backoff_seconds * (2 ** (attempt -1))
                logger.warning(f"Claude call failed on attempt {attempt}/{self.max_retries}: {e}")
                time.sleep(wait)

            except APIError as e:
                # These are not retriable, so we log and break immediately.
                logger.error(f"Claude call API error (not retriable): {e}")
                raise e
            
        logger.error(f"Claude call failed after {self.max_retries} attempts. Last error: {last_error}")
        raise RuntimeError(
            f"Claude call failed after {self.max_retries} attempts. Last error: {last_error}"
        ) from last_error
    
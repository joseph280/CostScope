"""
CostScope orchestrator.

Glues the LLM and database wrappers into a coherent question → answer flow.
For every user question, the orchestrator runs three small steps, each backed by a
versioned prompt file:

  1. ROUTE       — decide if the question needs database data
  2. SQL_GEN     — if yes, generate SQL against the schema
  3. SYNTHESIZE  — convert rows + question back into executive prose

If routing says "no data needed", we skip SQL_GEN and synthesize directly
from conversational context.

Prompt versioning: prompts live in app/prompts/<name>_v<N>.md. The loader
selects a version by name + integer, so prompt iterations are tracked
in git diff just like any other code change.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from wrappers.anthropic_wrapper import AnthropicWrapper
from wrappers.snowflake_wrapper import SnowflakeWrapper

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Result type 

@dataclass
class OrchestratorResult:
    """Everything one user question produced - answer plus diagnostics."""
    question: str
    answer: str
    used_data: bool
    sql: Optional[str] = None
    row_count: Optional[int] = None
    total_cost_usd: float = 0.0
    total_latency_ms: int = 0
    claude_calls: int = 0
    debug: dict = field(default_factory=dict)

# Prompt loader
def load_prompt(name: str, version: int) -> str:
    """Read a versioned prompt file from app/prompts/."""
    path = PROMPTS_DIR / f"{name}_v{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


# Orchestrator

class Orchestrator:
    """The brain. Routes questions, generates SQL, synthesizes answers."""

    def __init__(
            self,
            llm: Optional[AnthropicWrapper] = None,
            db: Optional[SnowflakeWrapper] = None,
            prompt_version: int = 1,
    ):
        self.llm = llm or AnthropicWrapper()
        self.db = db or SnowflakeWrapper()
        self.prompt_version = prompt_version

        # Load prompts once at startup - they don't change during a sesssion.
        self.prompt_routing = load_prompt("routing", prompt_version)
        self.prompt_sql_gen = load_prompt("sql_generation", prompt_version)
        self.prompt_synthesis = load_prompt("synthesis", prompt_version)


    # Public API

    def answer(self, question: str) -> OrchestratorResult:
        """Process one user question end-to-end."""
        logger.info("orchestrator_start question=%r", question)
        cost = 0.0
        latency = 0
        calls = 0

        # Step 1: route - does this question need data?
        routing = self.llm.complete(
            system=self.prompt_routing,
            user=question,
            max_tokens=128,
            temperature=0.0 # deterministic routing
        )
        cost += routing.cost_usd
        latency += routing.latency_ms
        calls += 1
        needs_data = self._parse_needs_data(routing.text)
        logger.info("orchestrator_routing question=%r needs_data=%s", question, needs_data)

        # Step 2a: pure conversation (skip SQL)

        if not needs_data:
            chat = self.llm.complete(
                system=(
                    "You are a finance analytics assistant for a CPG company. "
                    "Answer conversationally and briefly."
                ),
                user=question,
                max_tokens=512,
                temperature=0.3
            )
            cost += chat.cost_usd
            latency += chat.latency_ms
            calls += 1
            return OrchestratorResult(
                question=question,
                answer=chat.text,
                used_data=False,
                total_cost_usd=cost,
                total_latency_ms=latency,
                claude_calls=calls,
                debug={"routing_raw": routing.text}
            )
        
        # Step 2b: generate SQL, run it, synthesize answer
        sql_gen = self.llm.complete(
            system=self.prompt_sql_gen,
            user=question,
            max_tokens=512,
            temperature=0.0 # deterministic SQL
        )
        cost += sql_gen.cost_usd
        latency += sql_gen.latency_ms
        calls += 1
        sql = self._clean_sql(sql_gen.text)
        logger.info("orchestrator_sql_gen question=%r sql=%r", question, sql)

        # Step 3: execute SQL
        query_result = self.db.run_sql(sql)
        latency += query_result.latency_ms
        table_md = query_result.as_markdown_table(max_rows=20)

        # Step 4: synthesize
        synth_user = (
            f"Question: {question}\n\n"
            f"SQL executed: {sql}\n\n"
            f"Result rows:\n{table_md}\n\n"
        )
        synth = self.llm.complete(
            system=self.prompt_synthesis,
            user=synth_user,
            max_tokens=512,
            temperature=0.3
        )
        cost += synth.cost_usd
        latency += synth.latency_ms
        calls += 1
        return OrchestratorResult(
            question=question,
            answer=synth.text,
            used_data=True,
            sql=sql,
            row_count=query_result.row_count,
            total_cost_usd=cost,
            total_latency_ms=latency,
            claude_calls=calls,
            debug={
                "routing_raw": routing.text,
                "sql_gen": sql_gen.text
            }
        )
    
    # Helpers

    @staticmethod
    def _parse_needs_data(routing_text: str) -> bool:
        """
        Extract NEEDS_DATA: yes/no from the routing model's response.
        Default to True if parsing fails (better to over-fetch data than
        respond conversationally to a real question).
        """
        match = re.search(r"NEEDS_DATA:\s*(yes|no)", routing_text, re.IGNORECASE)
        if not match:
            logger.warning("routing_parse_fallback raw=%r", routing_text)
            return True
        return match.group(1).lower() == "yes"


    @staticmethod
    def _clean_sql(raw: str) -> str:
        """
        Defensive cleanup: strip markdown code fences if the model added them
        despite instructions, and trim whitespace.
        """
        text =raw.strip()
        text = re.sub(r"^```(?:sql)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        return text.strip()
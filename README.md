# CostScope

> Natural-language interface to enterprise cost data.
> Ask in English, get an executive-readable answer grounded in real numbers.

CostScope lets a finance user type a question — *"What did we spend on packaging in Q1?"* or *"Which business unit had the biggest marketing budget last year?"* — and returns a coherent prose answer backed by SQL executed against a structured cost database. The orchestrator routes every question through Claude three times: once to decide whether data lookup is needed, once to generate SQL if so, and once to synthesize the rows back into prose for the reader.

**Status:** working end-to-end. Demo project, not production-hardened. Built as a portfolio piece to demonstrate production-grade AI application engineering patterns: wrapper observability, retry semantics, prompt versioning, and evaluation harnesses.

---

## Architecture

```
                 ┌──────────────────────────────────┐
                 │  Phoenix LiveView (Elixir)       │
                 │  - stateful chat UI              │
                 │  - reactive WebSocket updates    │
                 │  - expandable SQL details        │
                 └──────────────┬───────────────────┘
                                │  HTTP / JSON
                                ▼
                 ┌──────────────────────────────────┐
                 │  FastAPI                         │
                 │   - POST /chat                   │
                 │   - GET  /health                 │
                 │   - GET  /docs (OpenAPI)         │
                 └──────────────┬───────────────────┘
                                │
                                ▼
                 ┌──────────────────────────────────┐
                 │  Orchestrator                    │
                 │                                  │
                 │   1. Route       (Claude)        │
                 │   2. Generate    (Claude)        │
                 │   3. Execute     (Snowflake)     │
                 │   4. Synthesize  (Claude)        │
                 └────────┬──────────────┬──────────┘
                          │              │
                          ▼              ▼
                 ┌──────────────┐ ┌──────────────┐
                 │  Snowflake   │ │  Anthropic   │
                 │  cost data   │ │  Claude      │
                 └──────────────┘ └──────────────┘
```

**Separation of concerns.** Snowflake is the data layer. Anthropic Claude is the AI layer. Phoenix is the UI layer and knows nothing about either. The wrapper interface is provider-agnostic — switching to Snowflake Cortex, OpenAI, or any other LLM provider is a config change, not a refactor.

---

## What it looks like in practice

```
User:  "Which business unit spent the most on marketing in 2025?"
         │
         ▼
[Claude #1 — route]      → needs_data: yes
         │
         ▼
[Claude #2 — SQL]        → SELECT business_unit, SUM(amount_usd) ...
                            WHERE cost_category='Marketing'
                            AND fiscal_year=2025
                            GROUP BY business_unit ORDER BY 2 DESC LIMIT 1;
         │
         ▼
[Snowflake exec]         → BEV_NA, $9,150,000.00
         │
         ▼
[Claude #3 — synthesize] → "BEV_NA led marketing spend in 2025 at
                            $9.15M, driven primarily by major-event
                            campaigns and a Q4 holiday push."
```

The UI shows the prose answer with an expandable section revealing the SQL that produced it, plus cost and latency metadata.

---

## Project layout

```
costscope/
├── app/
│   ├── config.py              # Pydantic Settings + pricing table
│   ├── main.py                # FastAPI: /chat, /health, /, /docs
│   ├── orchestrator.py        # Route → SQL gen → exec → synthesize
│   └── prompts/
│       ├── routing_v1.md
│       ├── sql_generation_v1.md
│       ├── sql_generation_v2.md
│       ├── synthesis_v1.md
│       └── CHANGELOG.md
├── wrappers/
│   ├── anthropic_wrapper.py   # Claude client with retry + telemetry
│   └── snowflake_wrapper.py   # Snowflake client with typed results
├── sql/
│   ├── costscope_setup.sql    # Database, schema, table, 50 demo rows
│   └── reference_queries.sql  # Hand-written queries the LLM should reproduce
├── eval/
│   ├── test_prompts.py        # Golden-question runner across prompt versions
│   ├── results_v1.json
│   └── results_v2.json
├── costscope_web/             # Phoenix LiveView frontend (Elixir)
│   └── lib/costscope_web/
│       ├── backend.ex         # HTTP client for the Python API
│       └── live/chat_live.ex  # Stateful chat LiveView
├── pyproject.toml             # uv manifest
├── uv.lock                    # exact-pinned lockfile
├── requirements.txt           # pip-compatible export
└── .env.example               # template (real .env is gitignored)
```

---

## Setup

### Backend (Python + FastAPI)

```bash
# Install uv if you do not have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and sync dependencies
git clone https://github.com/joseph280/costscope.git
cd costscope
uv sync

# Configure secrets
cp .env.example .env
# Open .env and fill in ANTHROPIC_API_KEY plus your Snowflake credentials

# Load the demo data into Snowflake (one-time)
# Run sql/costscope_setup.sql via Snowsight or snowsql

# Run the API on port 8000
uv run uvicorn app.main:app --reload --port 8000
```

Plain pip alternative:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# ...edit .env...
uvicorn app.main:app --reload --port 8000
```

Verify the backend:

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"costscope","version":"0.1.0"}
```

### Frontend (Phoenix LiveView)

```bash
# Install Elixir if you do not have it
brew install elixir

# In a second terminal, from the project root
cd costscope_web
mix deps.get
mix phx.server
```

Open http://localhost:4000 and start asking questions.

---

## Example questions

```bash
# Single aggregate
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"What was total marketing spend in Q4 2025?"}' | jq

# Comparison / grouping
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"Which business unit spent the most in 2025?"}' | jq

# Trend across periods
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"How did packaging costs trend across quarters?"}' | jq

# Conversational, no data lookup — routing returns needs_data:false
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"What is cost benchmarking?"}' | jq
```

The response envelope:

```json
{
  "question": "...",
  "answer":   "...",
  "used_data": true,
  "sql":       "SELECT ...",
  "row_count": 1,
  "cost_usd":  0.003421,
  "latency_ms": 4821,
  "claude_calls": 3
}
```

Every response carries its own observability metadata — cost, latency, call count, and the SQL that was executed. That is the same instrumentation production AI applications need.

---

## Design decisions

A few specific choices that are not obvious from the code, and would be worth discussing in a review.

### Why Anthropic Claude and not Snowflake Cortex AI Functions?

I started by evaluating Cortex AI Functions (`AI_COMPLETE`, `AI_SUMMARIZE`, `AI_CLASSIFY`) directly in Snowflake — running the AI on the same plane as the data is operationally attractive. On a Snowflake trial account those functions are gated behind a paid subscription, so I made a deliberate architectural choice: route all LLM operations through Anthropic Claude via a custom wrapper, and let Snowflake be the data layer only.

The trade-off is documented in `wrappers/anthropic_wrapper.py`: the public interface — a `complete()` method returning a typed `ClaudeResponse` — is provider-agnostic. Adding a `wrappers/cortex_wrapper.py` with the same shape and switching the orchestrator over is a config change, not a refactor. The same applies to OpenAI, Bedrock, or any other provider.

### Why no LangChain?

At this scope — three Claude calls and one SQL call per request — a framework adds dependency weight without saving code. The orchestration in `app/orchestrator.py` is roughly 150 lines of explicit Python that any reviewer can read top to bottom. Retry, observability, and cost tracking live in the wrapper, where they belong.

LangGraph starts paying off when the routing graph branches, when there is tool-calling fan-out, when checkpointing matters, or when human-in-the-loop steps need to be expressed declaratively. None of those are true here. I would reach for a framework at the point it saves more code than it adds.

### Why a Python orchestrator with a Phoenix frontend?

The AI ecosystem is Python-native. The Anthropic SDK, the Snowflake connector, every mainstream prompt-engineering and evaluation library — Python-first. Doing the AI layer in Elixir would mean rebuilding wrappers that Anthropic ships for free.

Phoenix LiveView is the right tool for the chat UI: reactive WebSocket updates with no client-side JavaScript on our side, stateful server-rendered components, and a clean process model for async work. The frontend talks to the Python backend over HTTP and knows nothing about Anthropic or Snowflake — a clean boundary that means either side can be rewritten independently.

### Why version prompts as files, not in code?

`app/prompts/*.md` files are version-controlled like any other source code. A prompt change shows up as a git diff. The orchestrator accepts a `prompt_version` argument so multiple versions can run side-by-side, and `eval/test_prompts.py` runs a fixed golden question set across both versions to compare cost, latency, and SQL output before promoting a new default. This is one of the smallest engineering changes that makes prompt iteration feel like real software development.

### Why no RAG or vector store?

The data is small, structured, and well-typed. SQL is the correct retrieval mechanism — there is nothing to embed and nothing to chunk. Adding a vector store here would be choosing the fashionable tool over the right one. RAG becomes relevant when the corpus is unstructured (PDFs, contracts, free-text notes); for cost transactions, SQL wins.

---

## Observability

Every Claude call and every SQL execution logs at `INFO` with structured fields: model, token counts, USD cost estimate, latency in milliseconds, attempt count for retries. One grep through the logs gets the full lifecycle of any request:

```
[INFO] anthropic_call_ok <ClaudeResponse model=claude-sonnet-4-5 tokens=72/41 cost=$0.00084 latency=1340ms attempts=1>
[INFO] orchestrator_sql sql='SELECT SUM(amount_usd)...'
[INFO] snowflake_query_ok <QueryResult rows=1 cols=1 latency=412ms>
[INFO] anthropic_call_ok <ClaudeResponse model=claude-sonnet-4-5 tokens=183/87 cost=$0.00185 latency=1820ms attempts=1>
```

`SessionStats` accumulates cost and latency across a run. In production that pattern would be replaced with Prometheus counters or OpenTelemetry spans — the same structured data, different sink.

---

## Cost characteristics

A single end-to-end question (3 Claude calls + 1 SQL execution against Snowflake) costs approximately **$0.001 to $0.005** at the time of writing, using Claude Sonnet 4.5 at $3/MTok input, $15/MTok output. Latency is dominated by the three serial LLM calls — typically 3 to 6 seconds end-to-end.

Conversational questions (routing returns `needs_data: false`) skip SQL generation and synthesis, costing closer to **$0.0003** in 1 to 2 seconds.

For a real deployment, the obvious next optimizations are caching repeated questions and running the routing step on a smaller, cheaper model like Haiku.

---

## What I would add next

In rough priority order, if this were a real product:

- **Authentication and per-user rate limiting.** Currently single-tenant with no auth — the API is wide open. Production needs at minimum a bearer token and a per-user spend cap.
- **Conversation memory.** v1 is single-turn. Multi-turn follow-ups ("and how does that compare to Q3?") need a thread store.
- **Result caching.** Same question twice → cached answer for the second. Redis or even an in-memory LRU.
- **Cortex Analyst integration.** With an Enterprise Snowflake account, Cortex Analyst's semantic model gives the LLM richer context for SQL generation than raw schema. Worth swapping in.
- **OpenTelemetry tracing.** Span per orchestrator step, exported to a collector. The structured logs are halfway there.
- **Evaluation harness expansion.** Answer-quality scoring (LLM-as-judge) on the golden set, tracked over time as prompts evolve. Currently only tracks cost, latency, and whether SQL changed.

---

## About the demo data

The demo data in `sql/costscope_setup.sql` is **synthetic CPG cost transactions** — 50 rows across 5 business units (BEV_NA, SNK_NA, BEV_INTL, APAC_MEA, LATAM), 7 cost categories (Packaging, Logistics, Ingredients, Labor, Marketing, IT, Energy), and 4 quarters of fiscal year 2025. Vendor names are plausible but fictional. Total spend is roughly $53M, weighted toward beverages North America.

No real company data is referenced anywhere in the repository.

---

## License

[MIT](./LICENSE) — use freely, attribution appreciated.
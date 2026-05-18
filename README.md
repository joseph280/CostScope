# CostScope

A natural-language interface to enterprise cost data.

CostScope lets a finance user ask questions in English — *"What did we spend on
packaging in Q1?"* — and get back coherent, executive-readable prose answers
grounded in real numbers from a structured cost database.

## Architecture

User question
   ↓ HTTP
Python FastAPI orchestrator
   ↓ Claude (route → SQL → synthesize)
Snowflake (cost data) + Anthropic (LLM)

The orchestrator separates concerns: Snowflake is the data layer, Anthropic
Claude is the AI layer. The wrapper interface is provider-agnostic — switching
to Snowflake Cortex or another LLM provider is a config change, not a refactor.

## Status

🚧 **In active development.** This is a portfolio project demonstrating
production-grade AI application engineering patterns: wrapper observability,
retry semantics, prompt versioning, evaluation harness. README and full setup
docs to follow.

## License

MIT — see [LICENSE](./LICENSE).

## Note

I generate requirements.txt from uv.lock so the repo works for anyone — uv users get the fast path, pip users still have a working install.
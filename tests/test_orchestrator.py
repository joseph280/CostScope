"""End-to-end smoke test for the orchestrator."""
import logging

from app.orchestrator import Orchestrator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


orch = Orchestrator()

questions = [
    # Pure-conversation question (should route to no-data)
    "What is cost benchmarking, in one sentence?",
    # Data question — total
    "What was total marketing spend in Q4 2025?",
    # Data question — comparison
    "Which business unit spent the most in 2025?",
]

for q in questions:
    print("\n" + "=" * 75)
    print(f"Q: {q}")
    print("=" * 75)
    result = orch.answer(q)
    print(f"\nA: {result.answer}\n")
    if result.used_data:
        print(f"   SQL:        {result.sql}")
        print(f"   Rows:       {result.row_count}")
    print(f"   Calls:      {result.claude_calls}")
    print(f"   Cost:       ${result.total_cost_usd:.5f}")
    print(f"   Latency:    {result.total_latency_ms}ms")
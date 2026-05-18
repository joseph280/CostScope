"""Smoke test for the Anthropic wrapper."""
import logging

from wrappers.anthropic_wrapper import AnthropicWrapper, stats

logging.basicConfig(
    level= logging.INFO,
    format="%(asctime)s [%(levelname)s %(name)s: %(message)s]"
)


client = AnthropicWrapper()

response = client.complete(
    system="You are a senior finance analyst ar a large CPG company.",
    user=(
        "In two sentences, explain why cost benchmarking across "
        "business units matters to a CFO."
    )
)

print("\n" + "=" * 70)
print("RESPONSE:\n", response.text)
print("\nMETADATA:", response)
print(f"\nSESSION: {stats.total_calls} calls, "
      f"${stats.total_cost_usd:.5f}, "
      f"{stats.total_latency_ms}ms")
print("=" * 70)
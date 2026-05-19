"""Smoke test for the Snowflake wrapper."""
import logging

from wrappers.snowflake_wrapper import SnowflakeWrapper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


sf = SnowflakeWrapper()

# Row count
result = sf.run_sql("SELECT COUNT(*) AS total FROM cost_transactions")
print("\n[Row count]")
print(result)
print("rows:", result.rows )

# Spend by BU
result = sf.run_sql("""
    SELECT business_unit, SUM(amount_usd) AS total_spend
    FROM cost_transactions
    WHERE fiscal_year = 2025
    GROUP BY business_unit
    ORDER BY total_spend DESC
""")
print("\n[Spend by BU]")
print(result)
print("\nAs markdown table:")
print(result.as_markdown_table())
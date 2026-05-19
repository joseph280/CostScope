"""
Snowflake wrapper for CostScope.

Design intent
-------------
A thin, typed wrapper around the snowflake-connector-python SDK with the
same production discipline as the Anthropic wrapper:

  1. Connection lifecycle managed via context manager — no leaked sessions
  2. Structured logging at every query — observability for slow-query triage
  3. Latency tracking on every execution
  4. Typed QueryResult dataclass with rows + column names + metadata
  5. Configuration externalized to app/config.py — no hardcoded credentials

Scope is deliberately minimal: one method `run_sql(query)` that takes a SQL
string and returns rows. 

Security note: this wrapper executes whatever SQL it's given. For a demo
that's acceptable because the SQL is LLM-generated against a tightly-scoped
read-only schema. In production this would sit behind a SQL-validation layer
or use a dedicated read-only role with row-level security.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, List, Optional

import snowflake.connector
from snowflake.connector import SnowflakeConnection
from snowflake.connector.errors import (
    DatabaseError,
    OperationalError,
    ProgrammingError,
)

from app.config import settings

logger = logging.getLogger(__name__)

# Result type

@dataclass
class QueryResult:
    """Structured result returned by SnowflakeWrapper.run_sql()."""
    rows: List[tuple]
    columns: List[str]
    latency_ms: int
    row_count: int
    sql: str = field(repr=False) # Excluded from repr to keep logs clean
    error: Optional[str] = None

    def __str__(self) -> str:
        if self.error:
            return f"<QueryResult error={self.error} latency={self.latency_ms}ms>"
        return (
            f"<QueryResult rows={len(self.rows)} "
            f"cols={len(self.columns)}"
            f"latency={self.latency_ms}ms>"
        )
    

    def as_dicts(self) -> List[dict]:
        """Convert rows to list of dicts keyed by column name."""
        return [dict(zip(self.columns, row)) for row in self.rows]
    

    def as_markdown_table(self, max_rows: int = 20) -> str:
        """
        Render result as a small markdown table (for LLM consumption).
        Useful for passing query results back to the LLM in a structured format. 
        Truncates to max_rows for readability. Does not include error results.
        """
        if not self.rows:
            return "_(no rows)_"

        header = "| " + " | ".join(self.columns) + " |"
        separator = "| " + " | ".join("---" for _ in self.columns) + " |"
        body_rows = self.rows[:max_rows]
        body = "\n".join(
            "| " + " | ".join(str(cell) for cell in row) + " |"
            for row in body_rows
        )
        suffix = (
            f"\n\n_(... {self.row_count} more rows)_"
            if self.row_count > max_rows else ""    
        )
        return f"{header}\n{separator}\n{body}{suffix}"
    
# Wrapper

class SnowflakeWrapper:
    """
    Thin wrapper around the Snowflake Python connector.

    Usage:
        sf = SnowflakeWrapper()
        result = sf.run_sql("SELECT COUNT(*) FROM cost_transactions")
        print(result.rows)        # [(50,)]
        print(result.as_dicts())  # [{"COUNT(*)": 50}]
    """

    def __init__(
        self,
        account: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        warehouse: Optional[str] = None,
        database: Optional[str] = None,
        schema: Optional[str] = None,
    ):
        self.account = account or settings.snowflake_account
        self.user = user or settings.snowflake_user
        self.password = password or settings.snowflake_password
        self.warehouse = warehouse or settings.snowflake_warehouse
        self.database = database or settings.snowflake_database
        self.schema = schema or settings.snowflake_schema

        missing = [
            name for name, value in {
                "snowflake_account": self.account,
                "snowflake_user": self.user,
                "snowflake_password": self.password
            }.items() if not value
        ]
        if missing:
            raise ValueError(
                f"Missing required Snowflake settings: {', '.join(missing)}. "
                f"Add them to .env."
            )
        
    # Connection lifecycle

    @contextmanager
    def _connect(self) -> Iterator[SnowflakeConnection]:
        """ 
        Open a Snowflake connection scoped to a single operation.
        Always closed cleanly, even on exception.

        For higher throughput we'd switch to a connection pool here, but
        for CostScope's request volume (interactive Q&A, not batch) a fresh
        connection per request is simpler and safe.
        """    
        conn: Optional[SnowflakeConnection] = None
        try:
            conn = snowflake.connector.connect(
                account=self.account,
                user=self.user,
                password=self.password,
                warehouse=self.warehouse,
                database=self.database,
                schema=self.schema,
            )
            yield conn
        except (DatabaseError, OperationalError, ProgrammingError) as e:
            logger.error(f"Snowflake connection error: {e}")
            raise
        finally:
            if conn:
                conn.close()

    # Public API

    def run_sql(self, sql: str) -> QueryResult:
        """
        Execute a SQL statement and return a structured QueryResult.

            Raises:
                ProgrammingError: invalid SQL or schema issue (4xx-equivalent)
                OperationalError: connection or transient infra issue (retryable)
                DatabaseError:    other Snowflake errors
        """
        start = time.perf_counter()
        with self._connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(sql)
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                latency_ms = int((time.perf_counter() - start) * 1000)
                result = QueryResult(
                    columns=columns,
                    rows=rows,
                    latency_ms=latency_ms,
                    row_count=len(rows),
                    sql=sql
                )
                logger.info(f"Executed SQL in {latency_ms}ms: {result}")
                return result
            
            except ProgrammingError as e:
                logger.error(f"snowflake_query_bad_sql: {e} | SQL: {sql}")
                raise
            except (OperationalError, DatabaseError) as e:
                logger.error(f"snowflake_query_error: {e} | SQL: {sql}")
                raise   
            finally:
                cursor.close()
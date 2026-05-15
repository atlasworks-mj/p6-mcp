import re

import pyodbc

from db import query


MAX_QUERY_ROWS = 200

FORBIDDEN_TOKENS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "EXEC",
    "EXECUTE",
    "TRUNCATE",
    "MERGE",
    "GRANT",
    "REVOKE",
    "DENY",
    "SHUTDOWN",
    "BACKUP",
    "RESTORE",
    "DBCC",
    "USE",
    "DECLARE",
    "SET",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SAVE",
    "KILL",
    "RECONFIGURE",
    "OPENROWSET",
    "OPENDATASOURCE",
    "OPENQUERY",
    "BULK",
    "XP_CMDSHELL",
    "SP_CONFIGURE",
    "WAITFOR",
}


class QueryValidationError(ValueError):
    pass


def _scan_sql(sql: str) -> tuple[list[str], bool]:
    """Return tokens outside strings/comments and whether a semicolon was found."""
    tokens: list[str] = []
    semicolon = False
    i = 0
    while i < len(sql):
        char = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        if char == "'":
            i += 1
            while i < len(sql):
                if sql[i] == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                if sql[i] == "'":
                    i += 1
                    break
                i += 1
            continue

        if char == "-" and nxt == "-":
            i = sql.find("\n", i + 2)
            if i == -1:
                break
            continue

        if char == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            i = len(sql) if end == -1 else end + 2
            continue

        if char == ";":
            semicolon = True
            i += 1
            continue

        if char.isalpha() or char == "_":
            start = i
            while i < len(sql) and (sql[i].isalnum() or sql[i] in {"_", "#", "@"}):
                i += 1
            tokens.append(sql[start:i].upper())
            continue

        i += 1

    return tokens, semicolon


def _validate_readonly_sql(sql: str) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise QueryValidationError("Provide a single read-only SELECT query.")

    stripped = sql.strip()
    tokens, has_semicolon = _scan_sql(stripped)
    if has_semicolon:
        raise QueryValidationError("Semicolons are not allowed. Submit one statement at a time.")
    if not tokens:
        raise QueryValidationError("Provide a single read-only SELECT query.")

    first = tokens[0]
    if first not in {"SELECT", "WITH"}:
        raise QueryValidationError(
            "Only read-only SELECT queries are allowed. Start with SELECT or WITH ... SELECT."
        )

    for token in tokens:
        if token in FORBIDDEN_TOKENS:
            raise QueryValidationError(f"Keyword {token} is not allowed in query_schedule.")

    if first == "WITH" and "SELECT" not in tokens[1:]:
        raise QueryValidationError("WITH queries must end in a read-only SELECT statement.")

    if re.search(r"\bSELECT\b[\s\S]*\bINTO\b[\s\S]*\bFROM\b", stripped, re.IGNORECASE):
        raise QueryValidationError("SELECT INTO is not allowed because it creates a table.")

    return stripped


def _safe_query_error(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, pyodbc.Error):
        if "42S02" in text:
            return "SQL Server rejected the query: unknown table or view."
        if "42S22" in text:
            return "SQL Server rejected the query: unknown column."
        if "HYT00" in text:
            return "SQL Server rejected the query: query timed out."
        if "08S01" in text:
            return "SQL Server rejected the query: database connection failed."
    return "SQL Server rejected the query. Check table names, column names, and syntax."


def query_schedule(sql: str) -> list[dict] | dict:
    """Execute a capped, read-only SQL query against the P6 database.

    Accepts a raw SELECT statement or WITH ... SELECT CTE and returns at most
    200 rows. Non-read-only statements are rejected before they reach SQL
    Server. The row cap is enforced by fetching only the first 200 rows, so
    SELECT DISTINCT, CTEs, ORDER BY, UNION, and window functions are not
    rewritten or damaged by the MCP layer.

    Key P6 tables and columns
    -------------------------

    PROJECT
        proj_id, proj_short_name, description, plan_start_date, scd_end_date,
        last_recalc_date, project_flag, clndr_id, critical_path_type,
        critical_drtn_hr_cnt, sum_base_proj_id, orig_proj_id, base_type_id

    TASK
        task_id, proj_id, task_code, task_name, task_type, status_code,
        target_start_date, target_end_date, act_start_date, act_end_date,
        target_drtn_hr_cnt, remain_drtn_hr_cnt, total_float_hr_cnt,
        free_float_hr_cnt, wbs_id, clndr_id, driving_path_flag, float_path,
        float_path_order, cstr_type, phys_complete_pct, early_start_date,
        early_end_date, late_start_date, late_end_date

    TASKPRED
        task_pred_id, task_id, pred_task_id, pred_proj_id, pred_type,
        lag_hr_cnt, comments, proj_id

    CALENDAR
        clndr_id, clndr_name, base_clndr_id, day_hr_cnt, week_hr_cnt,
        clndr_data

    PROJWBS
        wbs_id, proj_id, wbs_short_name, wbs_name, parent_wbs_id, obs_id,
        phase_id, status_code

    Activity codes and UDFs
        ACTVTYPE, ACTVCODE, TASKACTV, UDFTYPE, UDFVALUE, UDFCODE

    Resources and costs
        RSRC, ROLES, TASKRSRC, PROJCOST, RSRCRATE, ROLERATE

    Notes, documents, and risk
        TASKNOTE, TASKMEMO, WBSMEMO, MEMOTYPE, TASKPROC, DOCUMENT, TASKDOC,
        PROJISSU, PROJRISK, TASKRISK
    """
    try:
        return query(_validate_readonly_sql(sql), max_rows=MAX_QUERY_ROWS)
    except QueryValidationError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": _safe_query_error(exc)}

from db import query, query_single
from tools.common import get_project_settings, project_hours_per_day, schedule_task_type_condition


MAX_TOP_N = 500
MAX_SEARCH_TOP_N = 200
UDF_DISPLAY_EXPR = (
    "CASE "
    "WHEN uc.udf_code_id IS NOT NULL THEN CONCAT(COALESCE(uc.short_name, ''), "
    "CASE WHEN uc.short_name IS NOT NULL AND uc.udf_code_name IS NOT NULL THEN ' - ' ELSE '' END, "
    "COALESCE(uc.udf_code_name, '')) "
    "ELSE COALESCE(uv.udf_text, CONVERT(varchar, uv.udf_date, 23), CONVERT(varchar, uv.udf_number)) "
    "END"
)


def _clamp_top_n(value: int, default: int, maximum: int = MAX_TOP_N) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, maximum))


def _like(value: str) -> str:
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("[", "\\[")
    )
    return f"%{escaped}%"


def _project_metadata(project_id: int) -> dict:
    project = get_project_settings(project_id)
    return {
        "project": project,
        "hours_per_day": project_hours_per_day(project),
    }


def _not_found(project_id: int) -> dict | None:
    if query_single("SELECT proj_id FROM PROJECT WHERE proj_id = ?", (project_id,)):
        return None
    return {"error": f"Project {project_id} was not found.", "project_id": project_id}


def _where_like(column: str, value: str | None, params: list) -> str:
    if not value:
        return ""
    params.append(_like(value))
    return f" AND {column} LIKE ? ESCAPE '\\'"


def _code_scope_condition(alias: str, code_scope: str | None) -> tuple[str, str]:
    """Return a controlled activity-code scope filter and normalized scope label."""
    if not code_scope or str(code_scope).strip().lower() in {"all", "any", "*"}:
        return "", "all"

    normalized = str(code_scope).strip().lower().replace("-", "_")
    scope_map = {
        "global": "AS_Global",
        "as_global": "AS_Global",
        "project": "AS_Project",
        "as_project": "AS_Project",
    }
    p6_scope = scope_map.get(normalized)
    if not p6_scope:
        return "", "all"
    return f" AND {alias}.actv_code_type_scope = '{p6_scope}'", p6_scope


def _where_any_like(columns: list[str], value: str | None, params: list) -> str:
    if not value:
        return ""
    like = _like(value)
    params.extend([like] * len(columns))
    comparisons = " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in columns)
    return f" AND ({comparisons})"


def _wbs_path_cte() -> str:
    return """
        WITH wbs_paths AS (
            SELECT
                w.wbs_id,
                w.proj_id,
                w.parent_wbs_id,
                CAST(w.wbs_name AS varchar(max)) AS wbs_path
            FROM PROJWBS w
            WHERE w.parent_wbs_id IS NULL OR w.parent_wbs_id = 0

            UNION ALL

            SELECT
                child.wbs_id,
                child.proj_id,
                child.parent_wbs_id,
                CAST(parent.wbs_path + ' > ' + child.wbs_name AS varchar(max)) AS wbs_path
            FROM PROJWBS child
            JOIN wbs_paths parent ON parent.wbs_id = child.parent_wbs_id
        )
    """


def get_activity_codes(
    project_id: int,
    code_type: str | None = None,
    code_scope: str | None = None,
    include_values: bool = True,
    top_n: int = 200,
) -> dict:
    """Return activity code types used by a project, optionally with values."""
    missing = _not_found(project_id)
    if missing:
        return missing

    top_n = _clamp_top_n(top_n, 200)
    params: list = [project_id]
    type_filter = _where_like("at.actv_code_type", code_type, params)
    scope_filter, resolved_scope = _code_scope_condition("at", code_scope)

    code_types = query(f"""
        SELECT TOP {top_n}
            at.actv_code_type_id,
            at.actv_code_type AS code_type,
            at.actv_code_type_scope AS scope,
            at.proj_id AS type_project_id,
            at.actv_short_len,
            at.seq_num AS type_seq_num,
            COUNT(DISTINCT ta.task_id) AS activity_count,
            COUNT(DISTINCT ac.actv_code_id) AS value_count
        FROM ACTVTYPE at
        JOIN ACTVCODE ac ON ac.actv_code_type_id = at.actv_code_type_id
        JOIN TASKACTV ta
          ON ta.actv_code_type_id = ac.actv_code_type_id
         AND ta.actv_code_id = ac.actv_code_id
        JOIN TASK t
          ON t.task_id = ta.task_id
         AND t.proj_id = ta.proj_id
        WHERE t.proj_id = ?
          AND {schedule_task_type_condition("t")}
          {type_filter}
          {scope_filter}
        GROUP BY
            at.actv_code_type_id,
            at.actv_code_type,
            at.actv_code_type_scope,
            at.proj_id,
            at.actv_short_len,
            at.seq_num
        ORDER BY COUNT(DISTINCT ta.task_id) DESC, at.seq_num, at.actv_code_type
        """, tuple(params))

    values: list[dict] = []
    if include_values:
        value_params: list = [project_id]
        value_filter = _where_like("at.actv_code_type", code_type, value_params)
        value_scope_filter, _ = _code_scope_condition("at", code_scope)
        values = query(f"""
            SELECT TOP {top_n}
                at.actv_code_type_id,
                at.actv_code_type AS code_type,
                at.actv_code_type_scope AS scope,
                at.proj_id AS type_project_id,
                at.seq_num AS type_seq_num,
                ac.actv_code_id,
                ac.short_name AS code_value,
                ac.actv_code_name AS code_value_name,
                ac.parent_actv_code_id,
                pac.short_name AS parent_code_value,
                pac.actv_code_name AS parent_code_value_name,
                ac.seq_num AS code_seq_num,
                ac.color,
                COUNT(DISTINCT t.task_id) AS activity_count
            FROM ACTVTYPE at
            JOIN ACTVCODE ac ON ac.actv_code_type_id = at.actv_code_type_id
            JOIN TASKACTV ta
              ON ta.actv_code_type_id = ac.actv_code_type_id
             AND ta.actv_code_id = ac.actv_code_id
            JOIN TASK t
              ON t.task_id = ta.task_id
             AND t.proj_id = ta.proj_id
            LEFT JOIN ACTVCODE pac ON pac.actv_code_id = ac.parent_actv_code_id
            WHERE t.proj_id = ?
              AND {schedule_task_type_condition("t")}
              {value_filter}
              {value_scope_filter}
            GROUP BY
                at.actv_code_type_id,
                at.actv_code_type,
                at.actv_code_type_scope,
                at.proj_id,
                at.seq_num,
                ac.actv_code_id,
                ac.short_name,
                ac.actv_code_name,
                ac.parent_actv_code_id,
                pac.short_name,
                pac.actv_code_name,
                ac.seq_num,
                ac.color
            ORDER BY COUNT(DISTINCT t.task_id) DESC, at.seq_num, ac.seq_num, at.actv_code_type, ac.short_name
        """, tuple(value_params))

    return {
        **_project_metadata(project_id),
        "filters": {"project_id": project_id, "code_type": code_type, "code_scope": resolved_scope},
        "include_values": include_values,
        "top_n": top_n,
        "code_type_count": len(code_types),
        "value_count": len(values),
        "code_types": code_types,
        "values": values,
    }


def get_activity_code_usage(
    project_id: int,
    code_type: str | None = None,
    code_value: str | None = None,
    code_scope: str | None = None,
    top_n: int = 200,
) -> dict:
    """Return activities assigned to activity code values."""
    missing = _not_found(project_id)
    if missing:
        return missing

    top_n = _clamp_top_n(top_n, 200)
    params: list = [project_id]
    filters = [
        _where_like("at.actv_code_type", code_type, params),
        _where_any_like(["ac.short_name", "ac.actv_code_name"], code_value, params),
    ]
    scope_filter, resolved_scope = _code_scope_condition("at", code_scope)

    rows = query(f"""
        {_wbs_path_cte()}
        SELECT TOP {top_n}
            t.task_id,
            at.actv_code_type AS code_type,
            at.actv_code_type_id,
            at.actv_code_type_scope AS scope,
            ac.short_name AS code_value,
            ac.actv_code_id,
            ac.actv_code_name AS code_value_name,
            pac.short_name AS parent_code_value,
            pac.actv_code_name AS parent_code_value_name,
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            t.wbs_id,
            w.wbs_short_name,
            w.wbs_name,
            wp.wbs_path,
            CONVERT(varchar, t.target_start_date, 23) AS planned_start,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            CONVERT(varchar, t.act_start_date, 23) AS actual_start,
            CONVERT(varchar, t.act_end_date, 23) AS actual_finish,
            CONVERT(varchar, t.restart_date, 23) AS remaining_start,
            CONVERT(varchar, t.reend_date, 23) AS remaining_finish,
            t.total_float_hr_cnt AS total_float_hours,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days
        FROM TASK t
        JOIN TASKACTV ta
          ON ta.task_id = t.task_id
         AND ta.proj_id = t.proj_id
        JOIN ACTVCODE ac
          ON ac.actv_code_id = ta.actv_code_id
         AND ac.actv_code_type_id = ta.actv_code_type_id
        JOIN ACTVTYPE at ON at.actv_code_type_id = ta.actv_code_type_id
        LEFT JOIN ACTVCODE pac ON pac.actv_code_id = ac.parent_actv_code_id
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN wbs_paths wp ON wp.wbs_id = t.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ?
          AND {schedule_task_type_condition("t")}
          {''.join(filters)}
          {scope_filter}
        ORDER BY at.actv_code_type, ac.short_name, t.target_start_date, t.task_code
    """, tuple(params))

    return {
        **_project_metadata(project_id),
        "filters": {
            "project_id": project_id,
            "code_type": code_type,
            "code_value": code_value,
            "code_scope": resolved_scope,
        },
        "top_n": top_n,
        "row_count": len(rows),
        "activities": rows,
    }


def get_udfs(
    project_id: int,
    udf_name: str | None = None,
    include_values: bool = True,
    top_n: int = 200,
) -> dict:
    """Return task UDF definitions used by a project, optionally with values."""
    missing = _not_found(project_id)
    if missing:
        return missing

    top_n = _clamp_top_n(top_n, 200)
    params: list = [project_id]
    udf_filter = _where_any_like(["ut.udf_type_name", "ut.udf_type_label"], udf_name, params)

    udfs = query(f"""
        SELECT TOP {top_n}
            ut.udf_type_id,
            ut.udf_type_name,
            ut.udf_type_label,
            ut.logical_data_type,
            ut.table_name AS udf_type_table_name,
            ut.super_flag,
            ut.udf_code_short_len,
            MAX(CAST(ut.formula AS varchar(max))) AS formula,
            MAX(CAST(ut.indicator_expression AS varchar(max))) AS indicator_expression,
            ut.disp_data_flag,
            ut.disp_indicator_flag,
            ut.summary_method,
            (
                SELECT COUNT(*)
                FROM UDFCODE uc_count
                WHERE uc_count.udf_type_id = ut.udf_type_id
            ) AS coded_value_count,
            COUNT(DISTINCT uv.fk_id) AS activity_count,
            COUNT(*) AS value_count
        FROM UDFTYPE ut
        JOIN UDFVALUE uv ON uv.udf_type_id = ut.udf_type_id
        JOIN TASK t ON t.task_id = uv.fk_id
        WHERE COALESCE(uv.table_name, ut.table_name) = 'TASK'
          AND uv.proj_id = ?
          AND t.proj_id = ?
          AND {schedule_task_type_condition("t")}
          {udf_filter}
        GROUP BY
            ut.udf_type_id,
            ut.udf_type_name,
            ut.udf_type_label,
            ut.logical_data_type,
            ut.table_name,
            ut.super_flag,
            ut.udf_code_short_len,
            ut.disp_data_flag,
            ut.disp_indicator_flag,
            ut.summary_method
        ORDER BY COUNT(DISTINCT uv.fk_id) DESC, COALESCE(ut.udf_type_label, ut.udf_type_name)
    """, tuple([project_id, *params]))

    values: list[dict] = []
    if include_values:
        value_params: list = [project_id]
        value_filter = _where_any_like(["ut.udf_type_name", "ut.udf_type_label"], udf_name, value_params)
        values = query(f"""
            SELECT TOP {top_n}
                ut.udf_type_id,
                ut.udf_type_name,
                ut.udf_type_label,
                ut.logical_data_type,
                {UDF_DISPLAY_EXPR} AS udf_value,
                uc.udf_code_id,
                uc.short_name AS udf_code_short_name,
                uc.udf_code_name,
                uc.parent_udf_code_id,
                uc.seq_num AS udf_code_seq_num,
                uv.udf_text,
                CONVERT(varchar, uv.udf_date, 23) AS udf_date,
                uv.udf_number,
                COUNT(DISTINCT t.task_id) AS activity_count
            FROM UDFTYPE ut
            JOIN UDFVALUE uv ON uv.udf_type_id = ut.udf_type_id
            JOIN TASK t ON t.task_id = uv.fk_id
            LEFT JOIN UDFCODE uc ON uc.udf_code_id = uv.udf_code_id
            WHERE COALESCE(uv.table_name, ut.table_name) = 'TASK'
              AND uv.proj_id = ?
              AND t.proj_id = ?
              AND {schedule_task_type_condition("t")}
              {value_filter}
            GROUP BY
                ut.udf_type_id,
                ut.udf_type_name,
                ut.udf_type_label,
                ut.logical_data_type,
                uc.udf_code_id,
                uc.short_name,
                uc.udf_code_name,
                uc.parent_udf_code_id,
                uc.seq_num,
                uv.udf_text,
                uv.udf_date,
                uv.udf_number
            ORDER BY COUNT(DISTINCT t.task_id) DESC, COALESCE(ut.udf_type_label, ut.udf_type_name), udf_value
        """, tuple([project_id, *value_params]))

    return {
        **_project_metadata(project_id),
        "filters": {"project_id": project_id, "udf_name": udf_name},
        "include_values": include_values,
        "top_n": top_n,
        "udf_count": len(udfs),
        "value_count": len(values),
        "udfs": udfs,
        "values": values,
    }


def get_udf_usage(
    project_id: int,
    udf_name: str | None = None,
    udf_value: str | None = None,
    top_n: int = 200,
) -> dict:
    """Return activities with task UDF assignments."""
    missing = _not_found(project_id)
    if missing:
        return missing

    top_n = _clamp_top_n(top_n, 200)
    params: list = [project_id, project_id]
    filters = [
        _where_any_like(["ut.udf_type_name", "ut.udf_type_label"], udf_name, params),
        _where_any_like(
            [
                "uc.short_name",
                "uc.udf_code_name",
                "uv.udf_text",
                "CONVERT(varchar, uv.udf_date, 23)",
                "CONVERT(varchar, uv.udf_number)",
            ],
            udf_value,
            params,
        ),
    ]
    value_order_clause = ""
    if udf_value:
        value_order_clause = f"CASE WHEN {UDF_DISPLAY_EXPR} = ? THEN 0 ELSE 1 END, "
        params.append(str(udf_value))

    rows = query(f"""
        {_wbs_path_cte()}
        SELECT TOP {top_n}
            t.task_id,
            t.wbs_id,
            ut.udf_type_id,
            ut.udf_type_name,
            ut.udf_type_label,
            ut.table_name AS udf_type_table_name,
            uv.table_name AS udf_value_table_name,
            COALESCE(uv.table_name, ut.table_name) AS resolved_table_name,
            ut.logical_data_type,
            {UDF_DISPLAY_EXPR} AS udf_value,
            uc.udf_code_id,
            uc.short_name AS udf_code_short_name,
            uc.udf_code_name,
            uv.udf_text,
            CONVERT(varchar, uv.udf_date, 23) AS udf_date,
            uv.udf_number,
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            w.wbs_short_name,
            w.wbs_name,
            wp.wbs_path,
            CONVERT(varchar, t.target_start_date, 23) AS planned_start,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            CONVERT(varchar, t.act_start_date, 23) AS actual_start,
            CONVERT(varchar, t.act_end_date, 23) AS actual_finish,
            CONVERT(varchar, t.restart_date, 23) AS remaining_start,
            CONVERT(varchar, t.reend_date, 23) AS remaining_finish,
            t.total_float_hr_cnt AS total_float_hours,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days
        FROM UDFVALUE uv
        JOIN UDFTYPE ut ON ut.udf_type_id = uv.udf_type_id
        JOIN TASK t ON t.task_id = uv.fk_id
        LEFT JOIN UDFCODE uc ON uc.udf_code_id = uv.udf_code_id
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN wbs_paths wp ON wp.wbs_id = t.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE COALESCE(uv.table_name, ut.table_name) = 'TASK'
          AND uv.proj_id = ?
          AND t.proj_id = ?
          AND {schedule_task_type_condition("t")}
          {''.join(filters)}
        ORDER BY {value_order_clause}COALESCE(ut.udf_type_label, ut.udf_type_name), udf_value, t.target_start_date, t.task_code
    """, tuple(params))

    if udf_value:
        needle = str(udf_value)
        for row in rows:
            row["udf_value_is_exact_match"] = str(row.get("udf_value") or "") == needle

    return {
        **_project_metadata(project_id),
        "filters": {
            "project_id": project_id,
            "udf_name": udf_name,
            "udf_value": udf_value,
        },
        "top_n": top_n,
        "row_count": len(rows),
        "activities": rows,
    }


def _optional_text_matches(project_id: int, search_text: str | None, include_notes: bool, include_steps: bool) -> dict:
    if not search_text:
        return {"task_ids": [], "snippets": {}, "sources": [], "warnings": []}

    like = _like(search_text)
    task_ids: set[int] = set()
    snippets: dict[int, list[dict]] = {}
    sources: list[str] = []
    warnings: list[str] = []

    optional_queries: list[tuple[str, str]] = []
    if include_notes:
        optional_queries.extend([
            ("TASKNOTE", """
                SELECT TOP 500
                    tn.task_id,
                    'task_note' AS source,
                    LEFT(CAST(tn.task_notes AS varchar(max)), 500) AS matched_text
                FROM TASKNOTE tn
                JOIN TASK t ON t.task_id = tn.task_id
                WHERE tn.proj_id = ?
                  AND t.proj_id = ?
                  AND CAST(tn.task_notes AS varchar(max)) LIKE ? ESCAPE '\\'
            """),
            ("TASKMEMO", """
                SELECT TOP 500
                    tm.task_id,
                    COALESCE(mt.memo_type, 'task_memo') AS source,
                    LEFT(CAST(tm.task_memo AS varchar(max)), 500) AS matched_text
                FROM TASKMEMO tm
                JOIN TASK t ON t.task_id = tm.task_id
                LEFT JOIN MEMOTYPE mt ON mt.memo_type_id = tm.memo_type_id
                WHERE tm.proj_id = ?
                  AND t.proj_id = ?
                  AND CAST(tm.task_memo AS varchar(max)) LIKE ? ESCAPE '\\'
            """),
        ])
    if include_steps:
        optional_queries.append(("TASKPROC", """
            SELECT TOP 500
                tp.task_id,
                'task_step' AS source,
                LEFT(CAST(COALESCE(tp.proc_name, tp.proc_descr) AS varchar(max)), 500) AS matched_text
            FROM TASKPROC tp
            JOIN TASK t ON t.task_id = tp.task_id
            WHERE tp.proj_id = ?
              AND t.proj_id = ?
              AND CAST(COALESCE(tp.proc_name, tp.proc_descr) AS varchar(max)) LIKE ? ESCAPE '\\'
        """))

    for source, sql in optional_queries:
        try:
            rows = query(sql, (project_id, project_id, like))
        except Exception:
            warnings.append(f"{source} text search was skipped because the table or expected columns were unavailable.")
            continue
        sources.append(source)
        for row in rows:
            task_id = row.get("task_id")
            if task_id is None:
                continue
            task_ids.add(task_id)
            snippets.setdefault(task_id, []).append({
                "source": row.get("source"),
                "text": row.get("matched_text"),
            })

    return {
        "task_ids": sorted(task_ids),
        "snippets": snippets,
        "sources": sources,
        "warnings": warnings,
    }


def search_activities(
    project_id: int,
    search_text: str | None = None,
    code_type: str | None = None,
    code_value: str | None = None,
    code_scope: str | None = None,
    udf_name: str | None = None,
    udf_value: str | None = None,
    include_notes: bool = False,
    include_steps: bool = False,
    top_n: int = 50,
) -> dict:
    """Search activities by core fields, activity codes, UDFs, and optional text."""
    missing = _not_found(project_id)
    if missing:
        return missing

    top_n = _clamp_top_n(top_n, 50, MAX_SEARCH_TOP_N)
    params: list = [project_id]
    clauses = [f"t.proj_id = ?", schedule_task_type_condition("t")]
    matched_fields: list[str] = []
    text_matches = _optional_text_matches(project_id, search_text, include_notes, include_steps)

    if search_text:
        text_clauses = [
            "t.task_code LIKE ? ESCAPE '\\'",
            "t.task_name LIKE ? ESCAPE '\\'",
            "w.wbs_name LIKE ? ESCAPE '\\'",
            "w.wbs_short_name LIKE ? ESCAPE '\\'",
        ]
        like = _like(search_text)
        params.extend([like, like, like, like])
        if text_matches["task_ids"]:
            placeholders = ", ".join("?" for _ in text_matches["task_ids"])
            text_clauses.append(f"t.task_id IN ({placeholders})")
            params.extend(text_matches["task_ids"])
        clauses.append(f"({' OR '.join(text_clauses)})")
        matched_fields.append("activity_or_wbs_text")
        if text_matches["task_ids"]:
            matched_fields.append("notes_or_steps")

    code_scope_filter, resolved_code_scope = _code_scope_condition("at2", code_scope)
    if code_type or code_value or code_scope_filter:
        code_subparams: list = []
        code_filters = [
            _where_like("at2.actv_code_type", code_type, code_subparams),
            _where_any_like(["ac2.short_name", "ac2.actv_code_name"], code_value, code_subparams),
        ]
        clauses.append(f"""
            EXISTS (
                SELECT 1
                FROM TASKACTV ta2
                JOIN ACTVCODE ac2
                  ON ac2.actv_code_id = ta2.actv_code_id
                 AND ac2.actv_code_type_id = ta2.actv_code_type_id
                JOIN ACTVTYPE at2 ON at2.actv_code_type_id = ta2.actv_code_type_id
                WHERE ta2.task_id = t.task_id
                  AND ta2.proj_id = t.proj_id
                  {''.join(code_filters)}
                  {code_scope_filter}
            )
        """)
        params.extend(code_subparams)
        matched_fields.append("activity_code")

    if udf_name or udf_value:
        udf_subparams: list = []
        udf_filters = [
            _where_any_like(["ut2.udf_type_name", "ut2.udf_type_label"], udf_name, udf_subparams),
            _where_any_like(
                [
                    "uc2.short_name",
                    "uc2.udf_code_name",
                    "uv2.udf_text",
                    "CONVERT(varchar, uv2.udf_date, 23)",
                    "CONVERT(varchar, uv2.udf_number)",
                ],
                udf_value,
                udf_subparams,
            ),
        ]
        clauses.append(f"""
            EXISTS (
                SELECT 1
                FROM UDFVALUE uv2
                JOIN UDFTYPE ut2 ON ut2.udf_type_id = uv2.udf_type_id
                LEFT JOIN UDFCODE uc2 ON uc2.udf_code_id = uv2.udf_code_id
                WHERE uv2.fk_id = t.task_id
                  AND COALESCE(uv2.table_name, ut2.table_name) = 'TASK'
                  AND uv2.proj_id = t.proj_id
                  {''.join(udf_filters)}
            )
        """)
        params.extend(udf_subparams)
        matched_fields.append("udf")

    where_sql = "\n          AND ".join(clauses)
    rows = query(f"""
        {_wbs_path_cte()}
        SELECT TOP {top_n}
            t.task_id,
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            w.wbs_short_name,
            w.wbs_name,
            wp.wbs_path,
            CONVERT(varchar, t.target_start_date, 23) AS planned_start,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            CONVERT(varchar, t.early_start_date, 23) AS early_start,
            CONVERT(varchar, t.early_end_date, 23) AS early_finish,
            CONVERT(varchar, t.act_start_date, 23) AS actual_start,
            CONVERT(varchar, t.act_end_date, 23) AS actual_finish,
            t.target_drtn_hr_cnt AS planned_duration_hours,
            t.remain_drtn_hr_cnt AS remaining_duration_hours,
            t.total_float_hr_cnt AS total_float_hours,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days
        FROM TASK t
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN wbs_paths wp ON wp.wbs_id = t.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE {where_sql}
        ORDER BY t.target_start_date, t.task_code
    """, tuple(params))

    task_ids = [row["task_id"] for row in rows]
    code_rows = _activity_code_summaries(
        task_ids,
        code_type=code_type,
        code_value=code_value,
        code_scope=code_scope if (code_type or code_value or code_scope_filter) else None,
    )
    udf_rows = _udf_summaries(project_id, task_ids)
    code_by_task = _group_by_task_id(code_rows)
    udf_by_task = _group_by_task_id(udf_rows)

    results = []
    for row in rows:
        task_id = row.pop("task_id")
        result_matches = list(matched_fields)
        if search_text:
            result_matches.extend(_core_match_fields(row, search_text))
        results.append({
            **row,
            "activity_codes": code_by_task.get(task_id, []),
            "udfs": udf_by_task.get(task_id, []),
            "matched_fields": sorted(set(result_matches)),
            "matched_text": text_matches["snippets"].get(task_id, []),
        })

    return {
        **_project_metadata(project_id),
        "filters": {
            "project_id": project_id,
            "search_text": search_text,
            "code_type": code_type,
            "code_value": code_value,
            "code_scope": resolved_code_scope,
            "udf_name": udf_name,
            "udf_value": udf_value,
            "include_notes": include_notes,
            "include_steps": include_steps,
        },
        "top_n": top_n,
        "row_count": len(results),
        "optional_text_sources_searched": text_matches["sources"],
        "warnings": text_matches["warnings"],
        "activities": results,
    }


def _group_by_task_id(rows: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        item = dict(row)
        task_id = item.pop("task_id", None)
        if task_id is not None:
            grouped.setdefault(task_id, []).append(item)
    return grouped


def _activity_code_summaries(
    task_ids: list[int],
    code_type: str | None = None,
    code_value: str | None = None,
    code_scope: str | None = None,
) -> list[dict]:
    if not task_ids:
        return []
    placeholders = ", ".join("?" for _ in task_ids)
    params: list = list(task_ids)
    filters = [
        _where_like("at.actv_code_type", code_type, params),
        _where_any_like(["ac.short_name", "ac.actv_code_name"], code_value, params),
    ]
    scope_filter, _ = _code_scope_condition("at", code_scope)
    return query(f"""
        SELECT
            ta.task_id,
            at.actv_code_type_id,
            at.actv_code_type AS code_type,
            at.actv_code_type_scope AS scope,
            ac.actv_code_id,
            ac.short_name AS code_value,
            ac.actv_code_name AS code_value_name
        FROM TASKACTV ta
        JOIN ACTVCODE ac
          ON ac.actv_code_id = ta.actv_code_id
         AND ac.actv_code_type_id = ta.actv_code_type_id
        JOIN ACTVTYPE at ON at.actv_code_type_id = ta.actv_code_type_id
        WHERE ta.task_id IN ({placeholders})
          {''.join(filters)}
          {scope_filter}
        ORDER BY at.actv_code_type, ac.short_name
    """, tuple(params))


def _udf_summaries(project_id: int, task_ids: list[int]) -> list[dict]:
    if not task_ids:
        return []
    placeholders = ", ".join("?" for _ in task_ids)
    return query(f"""
        SELECT
            uv.fk_id AS task_id,
            ut.udf_type_name,
            ut.udf_type_label,
            {UDF_DISPLAY_EXPR} AS udf_value
        FROM UDFVALUE uv
        JOIN UDFTYPE ut ON ut.udf_type_id = uv.udf_type_id
        LEFT JOIN UDFCODE uc ON uc.udf_code_id = uv.udf_code_id
        WHERE COALESCE(uv.table_name, ut.table_name) = 'TASK'
          AND uv.proj_id = ?
          AND uv.fk_id IN ({placeholders})
        ORDER BY COALESCE(ut.udf_type_label, ut.udf_type_name), udf_value
    """, tuple([project_id, *task_ids]))


def _core_match_fields(row: dict, search_text: str) -> list[str]:
    needle = search_text.lower()
    fields = []
    for field in ("task_code", "task_name", "wbs_short_name", "wbs_name"):
        value = row.get(field)
        if value is not None and needle in str(value).lower():
            fields.append(field)
    return fields

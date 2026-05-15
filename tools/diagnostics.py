from db import query, query_single
from tools.common import (
    get_project_settings,
    project_hours_per_day,
    schedule_task_type_condition,
)


MAX_TOP_N = 200


def _limit(value: int, default: int = 50, maximum: int = MAX_TOP_N) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _lag_threshold(value: float, default: float = 5.0) -> float:
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(threshold, 3650.0))


def _status_clause(alias: str, include_completed: bool) -> str:
    return "" if include_completed else f"AND {alias}.status_code <> 'TK_Complete'"


def _count_value(row: dict | None, key: str) -> int:
    if not row or row.get(key) is None:
        return 0
    return int(row[key])


def _project_or_error(project_id: int) -> dict | None:
    return get_project_settings(project_id)


def _hard_constraint_sql(alias: str = "t") -> str:
    primary = f"UPPER(COALESCE(NULLIF(LTRIM(RTRIM({alias}.cstr_type)), ''), ''))"
    secondary = f"UPPER(COALESCE(NULLIF(LTRIM(RTRIM({alias}.cstr_type2)), ''), ''))"
    hard_tokens = (
        "'CS_MSO'",
        "'CS_MFO'",
        "'CS_MEO'",
        "'CS_MANDFIN'",
        "'CS_MANDSTART'",
        "'MANDATORY START'",
        "'MANDATORY FINISH'",
    )
    hard_list = ", ".join(hard_tokens)
    return f"""
        (
            {primary} IN ({hard_list})
            OR {secondary} IN ({hard_list})
            OR {primary} LIKE '%MAND%'
            OR {secondary} LIKE '%MAND%'
            OR {primary} LIKE '%MUST%'
            OR {secondary} LIKE '%MUST%'
        )
    """


def _has_constraint_sql(alias: str = "t") -> str:
    return (
        f"NULLIF(LTRIM(RTRIM({alias}.cstr_type)), '') IS NOT NULL "
        f"OR NULLIF(LTRIM(RTRIM({alias}.cstr_type2)), '') IS NOT NULL"
    )


def get_constraint_diagnostics(
    project_id: int,
    include_completed: bool = False,
    top_n: int = 50,
) -> dict:
    """Return date-constraint and date-control diagnostics for a P6 project.

    Args:
        project_id: The proj_id from get_project_list.
        include_completed: Include completed activities in diagnostics.
        top_n: Maximum rows returned per detail list, capped at 200.
    """
    top_n = _limit(top_n)
    project = _project_or_error(project_id)
    if not project:
        return {"error": f"Project {project_id} was not found.", "project_id": project_id}

    hours_per_day = project_hours_per_day(project)
    task_type_clause = schedule_task_type_condition("t")
    status_clause = _status_clause("t", include_completed)
    hard_constraint = _hard_constraint_sql("t")
    has_constraint = _has_constraint_sql("t")

    counts = query_single(f"""
        SELECT
            COUNT(*) AS filtered_activity_count,
            SUM(CASE WHEN t.status_code = 'TK_Complete' THEN 1 ELSE 0 END) AS completed_activity_count,
            SUM(CASE WHEN NULLIF(LTRIM(RTRIM(t.cstr_type)), '') IS NOT NULL THEN 1 ELSE 0 END) AS primary_constraint_count,
            SUM(CASE WHEN NULLIF(LTRIM(RTRIM(t.cstr_type2)), '') IS NOT NULL THEN 1 ELSE 0 END) AS secondary_constraint_count,
            SUM(CASE WHEN {hard_constraint} THEN 1 ELSE 0 END) AS hard_constraint_count,
            SUM(CASE WHEN t.expect_end_date IS NOT NULL THEN 1 ELSE 0 END) AS expected_finish_count,
            SUM(CASE WHEN t.suspend_date IS NOT NULL THEN 1 ELSE 0 END) AS suspend_date_count,
            SUM(CASE WHEN t.resume_date IS NOT NULL THEN 1 ELSE 0 END) AS resume_date_count,
            SUM(CASE
                    WHEN t.external_early_start_date IS NOT NULL
                      OR t.external_late_end_date IS NOT NULL
                    THEN 1 ELSE 0
                END) AS external_date_count,
            SUM(CASE WHEN t.control_updates_flag = 'Y' THEN 1 ELSE 0 END) AS control_updates_count,
            SUM(CASE
                    WHEN t.total_float_hr_cnt < 0
                     AND (
                        {has_constraint}
                        OR t.expect_end_date IS NOT NULL
                        OR t.external_early_start_date IS NOT NULL
                        OR t.external_late_end_date IS NOT NULL
                     )
                    THEN 1 ELSE 0
                END) AS negative_float_with_date_controls_count
        FROM TASK t
        WHERE t.proj_id = ?
          AND {task_type_clause}
          {status_clause}
    """, (project_id,))

    constraint_type_distribution = query(f"""
        SELECT
            constraint_slot,
            constraint_type,
            COUNT(*) AS activity_count
        FROM (
            SELECT 'primary' AS constraint_slot, t.cstr_type AS constraint_type
            FROM TASK t
            WHERE t.proj_id = ?
              AND {task_type_clause}
              {status_clause}
              AND NULLIF(LTRIM(RTRIM(t.cstr_type)), '') IS NOT NULL

            UNION ALL

            SELECT 'secondary' AS constraint_slot, t.cstr_type2 AS constraint_type
            FROM TASK t
            WHERE t.proj_id = ?
              AND {task_type_clause}
              {status_clause}
              AND NULLIF(LTRIM(RTRIM(t.cstr_type2)), '') IS NOT NULL
        ) constraint_rows
        GROUP BY constraint_slot, constraint_type
        ORDER BY activity_count DESC, constraint_slot, constraint_type
    """, (project_id, project_id))

    date_control_samples = query(f"""
        SELECT TOP {top_n}
            t.task_id,
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            w.wbs_short_name,
            w.wbs_name,
            c.clndr_name,
            ROUND(COALESCE(c.day_hr_cnt, {hours_per_day}), 2) AS calendar_hours_per_day,
            t.total_float_hr_cnt AS total_float_hours,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, {hours_per_day}, 8), 0), 1) AS total_float_days,
            t.free_float_hr_cnt AS free_float_hours,
            ROUND(t.free_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, {hours_per_day}, 8), 0), 1) AS free_float_days,
            CONVERT(varchar, t.target_start_date, 23) AS planned_start,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            CONVERT(varchar, t.early_start_date, 23) AS early_start,
            CONVERT(varchar, t.early_end_date, 23) AS early_finish,
            CONVERT(varchar, t.late_start_date, 23) AS late_start,
            CONVERT(varchar, t.late_end_date, 23) AS late_finish,
            t.cstr_type AS primary_constraint_type,
            CONVERT(varchar, t.cstr_date, 23) AS primary_constraint_date,
            t.cstr_type2 AS secondary_constraint_type,
            CONVERT(varchar, t.cstr_date2, 23) AS secondary_constraint_date,
            CONVERT(varchar, t.expect_end_date, 23) AS expected_finish,
            CONVERT(varchar, t.suspend_date, 23) AS suspend_date,
            CONVERT(varchar, t.resume_date, 23) AS resume_date,
            CONVERT(varchar, t.external_early_start_date, 23) AS external_early_start,
            CONVERT(varchar, t.external_late_end_date, 23) AS external_late_finish,
            t.control_updates_flag,
            CASE
                WHEN {hard_constraint} AND t.total_float_hr_cnt < 0 THEN 0
                WHEN {hard_constraint} THEN 1
                WHEN t.total_float_hr_cnt < 0 THEN 2
                WHEN t.expect_end_date IS NOT NULL
                  OR t.external_early_start_date IS NOT NULL
                  OR t.external_late_end_date IS NOT NULL THEN 3
                WHEN {has_constraint} THEN 4
                WHEN t.suspend_date IS NOT NULL OR t.resume_date IS NOT NULL THEN 5
                WHEN t.control_updates_flag = 'Y' THEN 6
                ELSE 9
            END AS severity_rank,
            CONCAT(
                CASE WHEN {hard_constraint} THEN 'hard_constraint;' ELSE '' END,
                CASE WHEN {has_constraint} AND NOT ({hard_constraint}) THEN 'constraint;' ELSE '' END,
                CASE WHEN t.total_float_hr_cnt < 0 THEN 'negative_float;' ELSE '' END,
                CASE WHEN t.expect_end_date IS NOT NULL THEN 'expected_finish;' ELSE '' END,
                CASE
                    WHEN t.external_early_start_date IS NOT NULL
                      OR t.external_late_end_date IS NOT NULL
                    THEN 'external_dates;' ELSE ''
                END,
                CASE
                    WHEN t.suspend_date IS NOT NULL OR t.resume_date IS NOT NULL
                    THEN 'suspend_resume;' ELSE ''
                END,
                CASE WHEN t.control_updates_flag = 'Y' THEN 'control_updates;' ELSE '' END
            ) AS risk_flags
        FROM TASK t
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ?
          AND {task_type_clause}
          {status_clause}
          AND (
              {has_constraint}
              OR t.expect_end_date IS NOT NULL
              OR t.suspend_date IS NOT NULL
              OR t.resume_date IS NOT NULL
              OR t.external_early_start_date IS NOT NULL
              OR t.external_late_end_date IS NOT NULL
              OR t.control_updates_flag = 'Y'
          )
        ORDER BY
            severity_rank,
            t.total_float_hr_cnt,
            COALESCE(
                t.cstr_date,
                t.cstr_date2,
                t.expect_end_date,
                t.external_late_end_date,
                t.external_early_start_date,
                t.target_end_date
            ),
            t.task_code
    """, (project_id,))

    expected_finish_samples = query(f"""
        SELECT TOP {top_n}
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            w.wbs_name,
            CONVERT(varchar, t.expect_end_date, 23) AS expected_finish,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            DATEDIFF(day, t.target_end_date, t.expect_end_date) AS expected_vs_planned_finish_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, {hours_per_day}, 8), 0), 1) AS total_float_days
        FROM TASK t
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ?
          AND {task_type_clause}
          {status_clause}
          AND t.expect_end_date IS NOT NULL
        ORDER BY
            CASE WHEN t.total_float_hr_cnt < 0 THEN 0 ELSE 1 END,
            t.total_float_hr_cnt,
            t.expect_end_date,
            t.task_code
    """, (project_id,))

    return {
        "project": project,
        "filters": {
            "project_id": project_id,
            "include_completed": include_completed,
            "included_task_types": ["TT_Task", "TT_Rsrc", "TT_Mile", "TT_FinMile"],
        },
        "thresholds": {
            "top_n": top_n,
            "hours_per_day": hours_per_day,
            "top_n_max": MAX_TOP_N,
        },
        "summary_counts": counts or {},
        "constraint_type_distribution": constraint_type_distribution,
        "date_control_samples": date_control_samples,
        "expected_finish_samples": expected_finish_samples,
    }


def _relationship_base_select(project_hours: float) -> str:
    return f"""
        SELECT
            tp.task_pred_id,
            tp.proj_id AS relationship_project_id,
            tp.pred_proj_id AS predecessor_project_id_from_relationship,
            tp.pred_type,
            tp.lag_hr_cnt AS lag_hours,
            ROUND(tp.lag_hr_cnt / NULLIF(COALESCE(pc.day_hr_cnt, {project_hours}, 8), 0), 1) AS lag_days_by_predecessor_calendar,
            ROUND(tp.lag_hr_cnt / NULLIF(COALESCE(sc.day_hr_cnt, {project_hours}, 8), 0), 1) AS lag_days_by_successor_calendar,
            ROUND(tp.lag_hr_cnt / NULLIF(COALESCE(sc.day_hr_cnt, pc.day_hr_cnt, {project_hours}, 8), 0), 1) AS lag_days,
            p.proj_id AS predecessor_project_id,
            pred_project.proj_short_name AS predecessor_project_short_name,
            p.task_id AS predecessor_task_id,
            p.task_code AS predecessor_code,
            p.task_name AS predecessor_name,
            p.task_type AS predecessor_task_type,
            p.status_code AS predecessor_status,
            pw.wbs_name AS predecessor_wbs,
            CONVERT(varchar, p.target_start_date, 23) AS predecessor_planned_start,
            CONVERT(varchar, p.target_end_date, 23) AS predecessor_planned_finish,
            CONVERT(varchar, p.act_start_date, 23) AS predecessor_actual_start,
            CONVERT(varchar, p.act_end_date, 23) AS predecessor_actual_finish,
            ROUND(p.total_float_hr_cnt / NULLIF(COALESCE(pc.day_hr_cnt, {project_hours}, 8), 0), 1) AS predecessor_total_float_days,
            s.proj_id AS successor_project_id,
            succ_project.proj_short_name AS successor_project_short_name,
            s.task_id AS successor_task_id,
            s.task_code AS successor_code,
            s.task_name AS successor_name,
            s.task_type AS successor_task_type,
            s.status_code AS successor_status,
            sw.wbs_name AS successor_wbs,
            CONVERT(varchar, s.target_start_date, 23) AS successor_planned_start,
            CONVERT(varchar, s.target_end_date, 23) AS successor_planned_finish,
            CONVERT(varchar, s.act_start_date, 23) AS successor_actual_start,
            CONVERT(varchar, s.act_end_date, 23) AS successor_actual_finish,
            ROUND(s.total_float_hr_cnt / NULLIF(COALESCE(sc.day_hr_cnt, {project_hours}, 8), 0), 1) AS successor_total_float_days
        FROM TASKPRED tp
        JOIN TASK s ON s.task_id = tp.task_id
        JOIN TASK p ON p.task_id = tp.pred_task_id
        LEFT JOIN PROJECT pred_project ON pred_project.proj_id = p.proj_id
        LEFT JOIN PROJECT succ_project ON succ_project.proj_id = s.proj_id
        LEFT JOIN PROJWBS pw ON pw.wbs_id = p.wbs_id
        LEFT JOIN PROJWBS sw ON sw.wbs_id = s.wbs_id
        LEFT JOIN CALENDAR pc ON pc.clndr_id = p.clndr_id
        LEFT JOIN CALENDAR sc ON sc.clndr_id = s.clndr_id
    """


def get_relationship_diagnostics(
    project_id: int,
    include_completed: bool = False,
    lag_day_threshold: float = 5.0,
    top_n: int = 50,
) -> dict:
    """Return relationship type, lag, and external-logic diagnostics.

    Args:
        project_id: The proj_id from get_project_list.
        include_completed: Include completed predecessor/successor activities.
        lag_day_threshold: Positive lag in days considered high.
        top_n: Maximum rows returned per detail list, capped at 200.
    """
    top_n = _limit(top_n)
    lag_day_threshold = _lag_threshold(lag_day_threshold)
    project = _project_or_error(project_id)
    if not project:
        return {"error": f"Project {project_id} was not found.", "project_id": project_id}

    hours_per_day = project_hours_per_day(project)
    pred_type_clause = schedule_task_type_condition("p")
    succ_type_clause = schedule_task_type_condition("s")
    pred_status_clause = _status_clause("p", include_completed)
    succ_status_clause = _status_clause("s", include_completed)
    base_select = _relationship_base_select(hours_per_day)
    relationship_where = f"""
        WHERE s.proj_id = ?
          AND {succ_type_clause}
          AND {pred_type_clause}
          {succ_status_clause}
          {pred_status_clause}
    """
    lag_days_expr = f"tp.lag_hr_cnt / NULLIF(COALESCE(sc.day_hr_cnt, pc.day_hr_cnt, {hours_per_day}, 8), 0)"
    is_fs_expr = "UPPER(COALESCE(tp.pred_type, '')) IN ('PR_FS', 'FS')"
    is_ss_expr = "UPPER(COALESCE(tp.pred_type, '')) IN ('PR_SS', 'SS')"
    is_ff_expr = "UPPER(COALESCE(tp.pred_type, '')) IN ('PR_FF', 'FF')"
    is_sf_expr = "UPPER(COALESCE(tp.pred_type, '')) IN ('PR_SF', 'SF')"

    summary_counts = query_single(f"""
        SELECT
            COUNT(*) AS relationship_count,
            SUM(CASE WHEN tp.lag_hr_cnt < 0 THEN 1 ELSE 0 END) AS negative_lag_count,
            SUM(CASE WHEN tp.lag_hr_cnt > 0 AND {lag_days_expr} >= ? THEN 1 ELSE 0 END) AS high_positive_lag_count,
            SUM(CASE WHEN ISNULL(tp.lag_hr_cnt, 0) = 0 THEN 1 ELSE 0 END) AS zero_lag_count,
            SUM(CASE WHEN NOT ({is_fs_expr}) THEN 1 ELSE 0 END) AS non_fs_count,
            SUM(CASE WHEN {is_ss_expr} THEN 1 ELSE 0 END) AS ss_count,
            SUM(CASE WHEN {is_ff_expr} THEN 1 ELSE 0 END) AS ff_count,
            SUM(CASE WHEN {is_sf_expr} THEN 1 ELSE 0 END) AS sf_count,
            SUM(CASE
                    WHEN COALESCE(tp.pred_proj_id, p.proj_id) <> ?
                      OR p.proj_id <> s.proj_id
                    THEN 1 ELSE 0
                END) AS external_predecessor_count
        FROM TASKPRED tp
        JOIN TASK s ON s.task_id = tp.task_id
        JOIN TASK p ON p.task_id = tp.pred_task_id
        LEFT JOIN CALENDAR pc ON pc.clndr_id = p.clndr_id
        LEFT JOIN CALENDAR sc ON sc.clndr_id = s.clndr_id
        {relationship_where}
    """, (lag_day_threshold, project_id, project_id))

    external_successor_count = query_single(f"""
        SELECT COUNT(*) AS cnt
        FROM TASKPRED tp
        JOIN TASK s ON s.task_id = tp.task_id
        JOIN TASK p ON p.task_id = tp.pred_task_id
        WHERE p.proj_id = ?
          AND {pred_type_clause}
          AND {succ_type_clause}
          {pred_status_clause}
          {succ_status_clause}
          AND s.proj_id <> ?
    """, (project_id, project_id))

    relationship_type_distribution = query(f"""
        SELECT
            tp.pred_type,
            COUNT(*) AS relationship_count,
            SUM(CASE WHEN tp.lag_hr_cnt < 0 THEN 1 ELSE 0 END) AS negative_lag_count,
            SUM(CASE WHEN tp.lag_hr_cnt > 0 AND {lag_days_expr} >= ? THEN 1 ELSE 0 END) AS high_positive_lag_count
        FROM TASKPRED tp
        JOIN TASK s ON s.task_id = tp.task_id
        JOIN TASK p ON p.task_id = tp.pred_task_id
        LEFT JOIN CALENDAR pc ON pc.clndr_id = p.clndr_id
        LEFT JOIN CALENDAR sc ON sc.clndr_id = s.clndr_id
        {relationship_where}
        GROUP BY tp.pred_type
        ORDER BY relationship_count DESC, tp.pred_type
    """, (lag_day_threshold, project_id))

    lag_stats = query_single(f"""
        SELECT
            MIN(ROUND({lag_days_expr}, 1)) AS min_lag_days,
            MAX(ROUND({lag_days_expr}, 1)) AS max_lag_days,
            AVG(ROUND({lag_days_expr}, 3)) AS avg_lag_days,
            SUM(CASE WHEN tp.lag_hr_cnt < 0 THEN tp.lag_hr_cnt ELSE 0 END) AS total_negative_lag_hours,
            SUM(CASE WHEN tp.lag_hr_cnt > 0 THEN tp.lag_hr_cnt ELSE 0 END) AS total_positive_lag_hours
        FROM TASKPRED tp
        JOIN TASK s ON s.task_id = tp.task_id
        JOIN TASK p ON p.task_id = tp.pred_task_id
        LEFT JOIN CALENDAR pc ON pc.clndr_id = p.clndr_id
        LEFT JOIN CALENDAR sc ON sc.clndr_id = s.clndr_id
        {relationship_where}
    """, (project_id,))

    negative_lag_rows = query(f"""
        SELECT TOP {top_n} *
        FROM (
            {base_select}
            {relationship_where}
              AND tp.lag_hr_cnt < 0
        ) rels
        ORDER BY lag_days, successor_code, predecessor_code
    """, (project_id,))

    high_lag_rows = query(f"""
        SELECT TOP {top_n} *
        FROM (
            {base_select}
            {relationship_where}
              AND tp.lag_hr_cnt > 0
              AND {lag_days_expr} >= ?
        ) rels
        ORDER BY lag_days DESC, successor_code, predecessor_code
    """, (project_id, lag_day_threshold))

    non_fs_rows = query(f"""
        SELECT TOP {top_n} *
        FROM (
            {base_select}
            {relationship_where}
              AND UPPER(COALESCE(tp.pred_type, '')) IN ('PR_SS', 'SS', 'PR_FF', 'FF', 'PR_SF', 'SF')
        ) rels
        ORDER BY pred_type, successor_code, predecessor_code
    """, (project_id,))

    external_predecessors = query(f"""
        SELECT TOP {top_n} *
        FROM (
            {base_select}
            {relationship_where}
              AND (
                  COALESCE(tp.pred_proj_id, p.proj_id) <> ?
                  OR p.proj_id <> s.proj_id
              )
        ) rels
        ORDER BY predecessor_project_id, successor_code, predecessor_code
    """, (project_id, project_id))

    external_successors = query(f"""
        SELECT TOP {top_n} *
        FROM (
            {base_select}
            WHERE p.proj_id = ?
              AND {pred_type_clause}
              AND {succ_type_clause}
              {pred_status_clause}
              {succ_status_clause}
              AND s.proj_id <> ?
        ) rels
        ORDER BY successor_project_id, predecessor_code, successor_code
    """, (project_id, project_id))

    relationship_count = _count_value(summary_counts, "relationship_count")
    summary = dict(summary_counts or {})
    summary["external_successor_count"] = _count_value(external_successor_count, "cnt")

    return {
        "project": project,
        "filters": {
            "project_id": project_id,
            "include_completed": include_completed,
            "included_task_types": ["TT_Task", "TT_Rsrc", "TT_Mile", "TT_FinMile"],
        },
        "thresholds": {
            "top_n": top_n,
            "top_n_max": MAX_TOP_N,
            "lag_day_threshold": lag_day_threshold,
            "hours_per_day_fallback": hours_per_day,
        },
        "summary_counts": summary,
        "lag_stats": lag_stats or {},
        "relationship_type_distribution": relationship_type_distribution,
        "relationship_density_note": (
            "Relationship scope is successors in the requested project; "
            "external_successors separately lists links where this project is the predecessor side."
        ),
        "relationship_count": relationship_count,
        "negative_lag_rows": negative_lag_rows,
        "high_lag_rows": high_lag_rows,
        "ss_ff_sf_rows": non_fs_rows,
        "external_predecessors": external_predecessors,
        "external_successors": external_successors,
    }

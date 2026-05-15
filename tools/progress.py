from db import query, query_single
from tools.common import schedule_task_type_condition


def _limit(value: int, default: int = 50, maximum: int = 200) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _count(sql: str, params: tuple) -> int:
    row = query_single(sql, params)
    return row["cnt"] if row and row.get("cnt") is not None else 0


def get_progress_diagnostics(project_id: int, lookback_days: int = 45, top_n: int = 50) -> dict:
    """Return update/progress diagnostics for a P6 project.

    Checks missed starts/finishes, out-of-sequence progress, recent actuals,
    active work past planned finish, and remaining-duration pressure.

    Args:
        project_id: The proj_id from get_project_list.
        lookback_days: Days before the project data date for recent progress.
        top_n: Maximum sample rows per diagnostic list.
    """
    top_n = _limit(top_n)
    try:
        lookback_days = max(1, min(int(lookback_days), 3650))
    except (TypeError, ValueError):
        lookback_days = 45

    project = query_single("""
        SELECT
            proj_id,
            proj_short_name,
            CONVERT(varchar, last_recalc_date, 23) AS data_date,
            CONVERT(varchar, DATEADD(day, -?, COALESCE(last_recalc_date, GETDATE())), 23) AS lookback_start
        FROM PROJECT
        WHERE proj_id = ?
    """, (lookback_days, project_id))
    if not project:
        return {"error": f"Project {project_id} was not found."}

    task_type_clause = schedule_task_type_condition("t")
    successor_type_clause = schedule_task_type_condition("s")
    common_where = """
        FROM TASK t
        JOIN PROJECT p ON p.proj_id = t.proj_id
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ?
          AND {task_type_clause}
    """
    common_where = common_where.format(task_type_clause=task_type_clause)

    missed_starts_count = _count(f"""
        SELECT COUNT(*) AS cnt
        {common_where}
          AND t.status_code <> 'TK_Complete'
          AND t.act_start_date IS NULL
          AND t.target_start_date < COALESCE(p.last_recalc_date, GETDATE())
    """, (project_id,))
    missed_finishes_count = _count(f"""
        SELECT COUNT(*) AS cnt
        {common_where}
          AND t.status_code <> 'TK_Complete'
          AND t.act_end_date IS NULL
          AND t.target_end_date < COALESCE(p.last_recalc_date, GETDATE())
    """, (project_id,))
    out_of_sequence_count = _count(f"""
        SELECT COUNT(*) AS cnt
        FROM TASK s
        JOIN TASKPRED tp ON tp.task_id = s.task_id
        JOIN TASK pred ON pred.task_id = tp.pred_task_id
        WHERE s.proj_id = ?
          AND {successor_type_clause}
          AND s.act_start_date IS NOT NULL
          AND pred.status_code <> 'TK_Complete'
    """, (project_id,))

    missed_starts = query(f"""
        SELECT TOP {top_n}
            t.task_code,
            t.task_name,
            t.task_type,
            w.wbs_name,
            t.status_code,
            CONVERT(varchar, t.target_start_date, 23) AS planned_start,
            CONVERT(varchar, p.last_recalc_date, 23) AS data_date,
            DATEDIFF(day, t.target_start_date, COALESCE(p.last_recalc_date, GETDATE())) AS days_late_to_start,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days
        {common_where}
          AND t.status_code <> 'TK_Complete'
          AND t.act_start_date IS NULL
          AND t.target_start_date < COALESCE(p.last_recalc_date, GETDATE())
        ORDER BY days_late_to_start DESC, t.task_code
    """, (project_id,))

    missed_finishes = query(f"""
        SELECT TOP {top_n}
            t.task_code,
            t.task_name,
            t.task_type,
            w.wbs_name,
            t.status_code,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            CONVERT(varchar, p.last_recalc_date, 23) AS data_date,
            DATEDIFF(day, t.target_end_date, COALESCE(p.last_recalc_date, GETDATE())) AS days_late_to_finish,
            ROUND(t.remain_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_duration_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days
        {common_where}
          AND t.status_code <> 'TK_Complete'
          AND t.act_end_date IS NULL
          AND t.target_end_date < COALESCE(p.last_recalc_date, GETDATE())
        ORDER BY days_late_to_finish DESC, t.task_code
    """, (project_id,))

    out_of_sequence = query(f"""
        SELECT TOP {top_n}
            s.task_code,
            s.task_name,
            s.task_type,
            CONVERT(varchar, s.act_start_date, 23) AS successor_actual_start,
            pred.task_code AS open_predecessor_code,
            pred.task_name AS open_predecessor_name,
            pred.task_type AS predecessor_task_type,
            pred.status_code AS predecessor_status,
            tp.pred_type,
            tp.lag_hr_cnt AS lag_hours,
            ROUND(tp.lag_hr_cnt / 8.0, 1) AS lag_days_8h,
            CONVERT(varchar, pred.target_end_date, 23) AS predecessor_planned_finish,
            CONVERT(varchar, pred.act_end_date, 23) AS predecessor_actual_finish
        FROM TASK s
        JOIN TASKPRED tp ON tp.task_id = s.task_id
        JOIN TASK pred ON pred.task_id = tp.pred_task_id
        WHERE s.proj_id = ?
          AND {successor_type_clause}
          AND s.act_start_date IS NOT NULL
          AND pred.status_code <> 'TK_Complete'
        ORDER BY s.act_start_date DESC, s.task_code
    """, (project_id,))

    recent_actuals = query(f"""
        SELECT TOP {top_n}
            t.task_code,
            t.task_name,
            t.task_type,
            w.wbs_name,
            t.status_code,
            CONVERT(varchar, t.act_start_date, 23) AS actual_start,
            CONVERT(varchar, t.act_end_date, 23) AS actual_finish,
            ROUND(t.act_this_per_work_qty, 2) AS actual_this_period_work_qty,
            ROUND(t.act_this_per_equip_qty, 2) AS actual_this_period_equip_qty
        {common_where}
          AND (
              t.act_start_date >= DATEADD(day, -?, COALESCE(p.last_recalc_date, GETDATE()))
              OR t.act_end_date >= DATEADD(day, -?, COALESCE(p.last_recalc_date, GETDATE()))
              OR ISNULL(t.act_this_per_work_qty, 0) > 0
              OR ISNULL(t.act_this_per_equip_qty, 0) > 0
          )
        ORDER BY COALESCE(t.act_end_date, t.act_start_date, t.update_date) DESC, t.task_code
    """, (project_id, lookback_days, lookback_days))

    stale_in_progress = query(f"""
        SELECT TOP {top_n}
            t.task_code,
            t.task_name,
            t.task_type,
            w.wbs_name,
            CONVERT(varchar, t.act_start_date, 23) AS actual_start,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            DATEDIFF(day, t.target_end_date, COALESCE(p.last_recalc_date, GETDATE())) AS days_past_planned_finish,
            ROUND(t.remain_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_duration_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days
        {common_where}
          AND t.status_code = 'TK_Active'
          AND t.target_end_date < COALESCE(p.last_recalc_date, GETDATE())
          AND ISNULL(t.remain_drtn_hr_cnt, 0) > 0
        ORDER BY days_past_planned_finish DESC, t.task_code
    """, (project_id,))

    remaining_duration_pressure = query(f"""
        SELECT TOP {top_n}
            t.task_code,
            t.task_name,
            t.task_type,
            w.wbs_name,
            t.status_code,
            ROUND(t.target_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS planned_duration_days,
            ROUND(t.remain_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_duration_days,
            ROUND((t.remain_drtn_hr_cnt - t.target_drtn_hr_cnt) / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_over_planned_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days
        {common_where}
          AND t.status_code <> 'TK_Complete'
          AND ISNULL(t.remain_drtn_hr_cnt, 0) > ISNULL(t.target_drtn_hr_cnt, 0)
        ORDER BY remaining_over_planned_days DESC, t.task_code
    """, (project_id,))

    date_anomalies = query(f"""
        SELECT TOP {top_n}
            t.task_code,
            t.task_name,
            t.task_type,
            w.wbs_name,
            t.status_code,
            CONVERT(varchar, t.act_start_date, 23) AS actual_start,
            CONVERT(varchar, t.act_end_date, 23) AS actual_finish,
            CASE
                WHEN t.status_code = 'TK_Complete' AND t.act_end_date IS NULL THEN 'complete_missing_actual_finish'
                WHEN t.status_code = 'TK_NotStart' AND (t.act_start_date IS NOT NULL OR t.act_end_date IS NOT NULL) THEN 'not_started_has_actuals'
                WHEN t.act_start_date > t.act_end_date THEN 'actual_start_after_actual_finish'
                ELSE 'unknown'
            END AS anomaly
        {common_where}
          AND (
              (t.status_code = 'TK_Complete' AND t.act_end_date IS NULL)
              OR (t.status_code = 'TK_NotStart' AND (t.act_start_date IS NOT NULL OR t.act_end_date IS NOT NULL))
              OR (t.act_start_date > t.act_end_date)
          )
        ORDER BY anomaly, t.task_code
    """, (project_id,))

    return {
        "project": project,
        "lookback_days": lookback_days,
        "counts": {
            "missed_starts": missed_starts_count,
            "missed_finishes": missed_finishes_count,
            "out_of_sequence_progress": out_of_sequence_count,
            "recent_actuals_sample": len(recent_actuals),
            "stale_in_progress_sample": len(stale_in_progress),
            "remaining_duration_pressure_sample": len(remaining_duration_pressure),
            "date_anomaly_sample": len(date_anomalies),
        },
        "missed_starts": missed_starts,
        "missed_finishes": missed_finishes,
        "out_of_sequence_progress": out_of_sequence,
        "recent_actuals": recent_actuals,
        "stale_in_progress": stale_in_progress,
        "remaining_duration_pressure": remaining_duration_pressure,
        "date_anomalies": date_anomalies,
    }

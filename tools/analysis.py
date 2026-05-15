from db import query, query_single
from tools.common import (
    critical_condition,
    get_project_settings,
    project_hours_per_day,
    schedule_task_type_condition,
)


def _limit(value: int, default: int = 10, maximum: int = 200) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def get_schedule_summary(project_id: int) -> dict:
    """Return high-level schedule metrics for a project."""
    project = get_project_settings(project_id)
    if not project:
        return {"error": f"Project {project_id} was not found."}

    critical_sql, critical_params, critical_basis, threshold_hours = critical_condition(project)
    task_type_clause = schedule_task_type_condition("t")

    counts = query_single(f"""
        SELECT
            COUNT(*) AS total_activities,
            SUM(CASE WHEN t.task_type = 'TT_Task' THEN 1 ELSE 0 END) AS task_count,
            SUM(CASE WHEN t.task_type = 'TT_Rsrc' THEN 1 ELSE 0 END) AS resource_dependent_count,
            SUM(CASE WHEN t.task_type IN ('TT_Mile', 'TT_FinMile') THEN 1 ELSE 0 END) AS milestone_count,
            SUM(CASE WHEN t.status_code = 'TK_Complete' THEN 1 ELSE 0 END) AS complete,
            SUM(CASE WHEN t.status_code = 'TK_Active' THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN t.status_code = 'TK_NotStart' THEN 1 ELSE 0 END) AS not_started,
            SUM(CASE WHEN t.status_code <> 'TK_Complete' AND {critical_sql} THEN 1 ELSE 0 END) AS critical_activities,
            CONVERT(varchar, MIN(t.target_start_date), 23) AS earliest_start,
            CONVERT(varchar, MAX(t.target_end_date), 23) AS latest_finish
        FROM TASK t
        WHERE t.proj_id = ? AND {task_type_clause}
    """, (*critical_params, project_id))

    relationships = query_single("""
        SELECT COUNT(*) AS relationship_count
        FROM TASKPRED tp
        JOIN TASK t ON tp.task_id = t.task_id
        WHERE t.proj_id = ?
    """, (project_id,))

    open_starts = query_single(f"""
        SELECT COUNT(*) AS cnt
        FROM TASK t
        WHERE t.proj_id = ?
          AND {task_type_clause}
          AND t.status_code <> 'TK_Complete'
          AND NOT EXISTS (SELECT 1 FROM TASKPRED tp WHERE tp.task_id = t.task_id)
    """, (project_id,))

    open_finishes = query_single(f"""
        SELECT COUNT(*) AS cnt
        FROM TASK t
        WHERE t.proj_id = ?
          AND {task_type_clause}
          AND t.status_code <> 'TK_Complete'
          AND NOT EXISTS (SELECT 1 FROM TASKPRED tp WHERE tp.pred_task_id = t.task_id)
    """, (project_id,))

    if not counts:
        return {"error": f"No schedule activities found for project_id {project_id}"}

    total = counts["total_activities"] or 0
    pct_complete = round((counts["complete"] or 0) / total * 100, 1) if total else 0
    hours_per_day = project_hours_per_day(project)

    return {
        **counts,
        "percent_complete": pct_complete,
        "relationship_count": relationships["relationship_count"] if relationships else 0,
        "open_start_count": open_starts["cnt"] if open_starts else 0,
        "open_finish_count": open_finishes["cnt"] if open_finishes else 0,
        "critical_basis": critical_basis,
        "critical_path_type": project.get("critical_path_type"),
        "critical_threshold_hours": threshold_hours,
        "critical_threshold_days": round(threshold_hours / hours_per_day, 2),
        "critical_threshold_defaulted": (
            project.get("critical_path_type") != "CT_DrivPath"
            and project.get("critical_drtn_hr_cnt") is None
        ),
        "included_task_types": ["TT_Task", "TT_Rsrc", "TT_Mile", "TT_FinMile"],
    }


def analyze_schedule(project_id: int) -> dict:
    """Run a comprehensive schedule quality analysis and return structured data."""
    summary = get_schedule_summary(project_id)
    if "error" in summary:
        return summary

    project = get_project_settings(project_id)
    critical_sql, critical_params, critical_basis, threshold_hours = critical_condition(project)
    task_type_clause = schedule_task_type_condition("t")
    hours_per_day = project_hours_per_day(project)
    near_limit_hours = threshold_hours + (10 * hours_per_day)
    normal_limit_hours = threshold_hours + (44 * hours_per_day)
    total = summary["total_activities"]

    float_dist = query(f"""
        SELECT bucket AS float_range, COUNT(*) AS count
        FROM (
            SELECT
                CASE
                    WHEN t.total_float_hr_cnt IS NULL THEN 'unknown_float'
                    WHEN t.total_float_hr_cnt < 0 THEN 'negative_float'
                    WHEN {critical_sql} THEN 'p6_critical'
                    WHEN t.total_float_hr_cnt <= ? THEN 'near_critical'
                    WHEN t.total_float_hr_cnt <= ? THEN 'normal_float'
                    ELSE 'high_float'
                END AS bucket
            FROM TASK t
            WHERE t.proj_id = ?
              AND {task_type_clause}
              AND t.status_code <> 'TK_Complete'
        ) buckets
        GROUP BY bucket
    """, (*critical_params, near_limit_hours, normal_limit_hours, project_id))

    float_data_quality = query_single(f"""
        SELECT
            SUM(CASE WHEN t.total_float_hr_cnt IS NULL THEN 1 ELSE 0 END) AS unknown_float_count,
            SUM(CASE WHEN t.total_float_hr_cnt IS NOT NULL THEN 1 ELSE 0 END) AS known_float_count
        FROM TASK t
        WHERE t.proj_id = ?
          AND {task_type_clause}
          AND t.status_code <> 'TK_Complete'
    """, (project_id,))

    constraints = query(f"""
        SELECT cstr_type, COUNT(*) AS count
        FROM TASK t
        WHERE t.proj_id = ? AND {task_type_clause} AND t.cstr_type IS NOT NULL
        GROUP BY cstr_type
    """, (project_id,))

    critical_path = query_single(f"""
        SELECT
            SUM(t.target_drtn_hr_cnt) AS critical_path_duration_hours,
            ROUND(SUM(t.target_drtn_hr_cnt) / ?, 1) AS critical_path_duration_days,
            COUNT(*) AS critical_path_activities
        FROM TASK t
        WHERE t.proj_id = ?
          AND {task_type_clause}
          AND t.status_code <> 'TK_Complete'
          AND {critical_sql}
    """, (hours_per_day, project_id, *critical_params))

    logic_density = round(summary["relationship_count"] / total, 2) if total else 0

    top_risk_activities = query(f"""
        SELECT TOP 5
            t.task_code,
            t.task_name,
            t.task_type,
            t.total_float_hr_cnt AS float_hours,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS float_days
        FROM TASK t
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ?
          AND {task_type_clause}
          AND t.status_code <> 'TK_Complete'
          AND t.total_float_hr_cnt IS NOT NULL
        ORDER BY t.total_float_hr_cnt ASC, t.task_code
    """, (project_id,))

    longest_remaining = query(f"""
        SELECT TOP 5
            t.task_code,
            t.task_name,
            t.task_type,
            t.remain_drtn_hr_cnt AS remaining_hours,
            t.total_float_hr_cnt AS float_hours,
            ROUND(t.remain_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS float_days
        FROM TASK t
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ? AND {task_type_clause} AND t.status_code <> 'TK_Complete'
        ORDER BY t.remain_drtn_hr_cnt DESC, t.task_code
    """, (project_id,))

    wbs_risk_areas = query(f"""
        SELECT TOP 5
            w.wbs_name,
            SUM(CASE WHEN t.total_float_hr_cnt < 0 THEN 1 ELSE 0 END) AS neg_float_count,
            COUNT(*) AS total_activities
        FROM TASK t
        JOIN PROJWBS w ON t.wbs_id = w.wbs_id
        WHERE t.proj_id = ? AND {task_type_clause} AND t.status_code <> 'TK_Complete'
        GROUP BY w.wbs_name
        HAVING SUM(CASE WHEN t.total_float_hr_cnt < 0 THEN 1 ELSE 0 END) > 0
        ORDER BY neg_float_count DESC, total_activities DESC
    """, (project_id,))

    biggest_risk = _determine_biggest_risk(
        summary, float_dist, top_risk_activities, longest_remaining, total
    )

    return {
        "summary": summary,
        "float_distribution": float_dist,
        "float_distribution_basis": {
            "critical_bucket": critical_basis,
            "near_critical_window_days_after_critical_threshold": 10,
            "normal_window_days_after_critical_threshold": 44,
            "hours_per_day": hours_per_day,
            "null_float_bucket": "unknown_float",
        },
        "float_data_quality": float_data_quality,
        "constraints": constraints,
        "critical_path": critical_path,
        "logic_density": logic_density,
        "quality_flags": {
            "has_open_starts": summary["open_start_count"] > 0,
            "has_open_finishes": summary["open_finish_count"] > 0,
            "low_logic_density": logic_density < 1.4,
            "high_critical_ratio": (
                summary["critical_activities"] / total > 0.25 if total else False
            ),
        },
        "top_risk_activities": top_risk_activities,
        "longest_remaining": longest_remaining,
        "wbs_risk_areas": wbs_risk_areas,
        "biggest_risk": biggest_risk,
    }


def _determine_biggest_risk(
    summary: dict,
    float_dist: list[dict],
    top_risk_activities: list[dict],
    longest_remaining: list[dict],
    total: int,
) -> str:
    unknown_count = 0
    known_count = 0
    for fd in float_dist:
        if fd["float_range"] == "unknown_float":
            unknown_count = fd["count"]
        else:
            known_count += fd["count"]
    if unknown_count and known_count == 0:
        return (
            "No usable total-float data is populated for incomplete activities; "
            "use relationship, open-end, and constraint diagnostics before drawing critical-path conclusions"
        )

    first_float = top_risk_activities[0].get("float_days") if top_risk_activities else None
    if first_float is not None and first_float < 0:
        act = top_risk_activities[0]
        remaining_info = ""
        for lr in longest_remaining:
            if lr["task_code"] == act["task_code"]:
                remaining_info = f" with {lr['remaining_days']}d remaining duration"
                break
        return (
            f"{act['task_name']} ({act['task_code']}) at {act['float_days']}d float"
            f"{remaining_info}"
        )

    critical_count = summary.get("critical_activities", 0)
    if total and critical_count:
        critical_ratio = round(critical_count / total * 100, 0)
        if critical_ratio > 25:
            basis = summary.get("critical_basis", "P6 critical")
            return (
                f"{int(critical_ratio)}% critical ratio ({critical_count} of {total} "
                f"incomplete schedule items, basis: {basis}) indicates fragile schedule"
            )

    if longest_remaining:
        lr = longest_remaining[0]
        float_days = lr.get("float_days", 999)
        if float_days is not None and float_days <= 10:
            return (
                f"{lr['task_name']} ({lr['task_code']}) has {lr['remaining_days']}d "
                f"remaining with only {float_days}d float"
            )

    neg_count = 0
    for fd in float_dist:
        if fd["float_range"] == "negative_float":
            neg_count = fd["count"]
            break
    if neg_count > 0:
        return f"{neg_count} activities with negative float require immediate attention"

    if summary.get("open_start_count", 0) > 5:
        return f"{summary['open_start_count']} open starts -- logic gaps may mask critical path"

    return "No dominant single risk identified; schedule is relatively balanced"


def compare_schedules(baseline_id: int, update_id: int, top_n: int = 10) -> dict:
    """Compare two schedule versions and return variance analysis."""
    top_n = _limit(top_n)
    baseline_proj = query_single("""
        SELECT
            CONVERT(varchar, scd_end_date, 23) AS scd_end_date,
            CONVERT(varchar, plan_start_date, 23) AS plan_start_date
        FROM PROJECT
        WHERE proj_id = ?
    """, (baseline_id,))

    update_proj = query_single("""
        SELECT
            CONVERT(varchar, scd_end_date, 23) AS scd_end_date,
            CONVERT(varchar, plan_start_date, 23) AS plan_start_date
        FROM PROJECT
        WHERE proj_id = ?
    """, (update_id,))

    if not baseline_proj or not update_proj:
        return {"error": "One or both project IDs not found"}

    task_type_clause = schedule_task_type_condition("")

    finish_delta = query_single("""
        SELECT DATEDIFF(day,
            (SELECT scd_end_date FROM PROJECT WHERE proj_id = ?),
            (SELECT scd_end_date FROM PROJECT WHERE proj_id = ?)
        ) AS finish_delta_days
    """, (baseline_id, update_id))

    counts = query_single(f"""
        SELECT
            (SELECT COUNT(*) FROM TASK WHERE proj_id = ? AND {task_type_clause}) AS baseline_activities,
            (SELECT COUNT(*) FROM TASK WHERE proj_id = ? AND {task_type_clause}) AS update_activities
    """, (baseline_id, update_id))

    added = query_single(f"""
        SELECT COUNT(*) AS cnt
        FROM TASK u
        WHERE u.proj_id = ? AND {schedule_task_type_condition("u")}
          AND NOT EXISTS (
              SELECT 1 FROM TASK b
              WHERE b.proj_id = ? AND {schedule_task_type_condition("b")}
                AND b.task_code = u.task_code
          )
    """, (update_id, baseline_id))

    removed = query_single(f"""
        SELECT COUNT(*) AS cnt
        FROM TASK b
        WHERE b.proj_id = ? AND {schedule_task_type_condition("b")}
          AND NOT EXISTS (
              SELECT 1 FROM TASK u
              WHERE u.proj_id = ? AND {schedule_task_type_condition("u")}
                AND u.task_code = b.task_code
          )
    """, (baseline_id, update_id))

    baseline_float = _get_float_distribution(baseline_id)
    update_float = _get_float_distribution(update_id)

    neg_float_baseline = query_single(f"""
        SELECT COUNT(*) AS cnt
        FROM TASK t
        WHERE t.proj_id = ? AND {schedule_task_type_condition("t")}
          AND t.status_code <> 'TK_Complete'
          AND t.total_float_hr_cnt < 0
    """, (baseline_id,))

    neg_float_update = query_single(f"""
        SELECT COUNT(*) AS cnt
        FROM TASK t
        WHERE t.proj_id = ? AND {schedule_task_type_condition("t")}
          AND t.status_code <> 'TK_Complete'
          AND t.total_float_hr_cnt < 0
    """, (update_id,))

    completion = query_single(f"""
        SELECT
            SUM(CASE WHEN status_code = 'TK_Complete' THEN 1 ELSE 0 END) AS complete,
            SUM(CASE WHEN status_code = 'TK_Active' THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN status_code = 'TK_NotStart' THEN 1 ELSE 0 END) AS not_started
        FROM TASK
        WHERE proj_id = ? AND {task_type_clause}
    """, (update_id,))

    top_slipped = query(f"""
        SELECT TOP {top_n}
            u.task_code,
            u.task_name,
            u.task_type,
            CONVERT(varchar, b.target_end_date, 23) AS baseline_finish,
            CONVERT(varchar, u.target_end_date, 23) AS update_finish,
            DATEDIFF(day, b.target_end_date, u.target_end_date) AS slip_days
        FROM TASK u
        JOIN TASK b ON u.task_code = b.task_code
            AND b.proj_id = ? AND {schedule_task_type_condition("b")}
        WHERE u.proj_id = ? AND {schedule_task_type_condition("u")}
          AND u.target_end_date > b.target_end_date
        ORDER BY DATEDIFF(day, b.target_end_date, u.target_end_date) DESC
    """, (baseline_id, update_id))

    return {
        "filters": {"baseline_id": baseline_id, "update_id": update_id, "top_n": top_n},
        "baseline_finish": baseline_proj["scd_end_date"],
        "update_finish": update_proj["scd_end_date"],
        "finish_delta_days": finish_delta["finish_delta_days"] if finish_delta else None,
        "baseline_activities": counts["baseline_activities"] if counts else 0,
        "update_activities": counts["update_activities"] if counts else 0,
        "activities_added": added["cnt"] if added else 0,
        "activities_removed": removed["cnt"] if removed else 0,
        "float_shift": {
            "baseline": baseline_float,
            "update": update_float,
        },
        "neg_float_trend": {
            "baseline": neg_float_baseline["cnt"] if neg_float_baseline else 0,
            "update": neg_float_update["cnt"] if neg_float_update else 0,
        },
        "completion_status": {
            "complete": completion["complete"] if completion else 0,
            "in_progress": completion["in_progress"] if completion else 0,
            "not_started": completion["not_started"] if completion else 0,
        },
        "included_task_types": ["TT_Task", "TT_Rsrc", "TT_Mile", "TT_FinMile"],
        "top_slipped_activities": top_slipped,
    }


def _get_float_distribution(project_id: int) -> dict:
    """Return P6-aware float bucket counts for a project."""
    project = get_project_settings(project_id)
    critical_sql, critical_params, _, threshold_hours = critical_condition(project)
    hours_per_day = project_hours_per_day(project)
    near_limit_hours = threshold_hours + (10 * hours_per_day)
    normal_limit_hours = threshold_hours + (44 * hours_per_day)

    rows = query(f"""
        SELECT bucket, COUNT(*) AS count
        FROM (
            SELECT
                CASE
                    WHEN t.total_float_hr_cnt < 0 THEN 'negative'
                    WHEN {critical_sql} THEN 'critical'
                    WHEN t.total_float_hr_cnt <= ? THEN 'near_critical'
                    WHEN t.total_float_hr_cnt <= ? THEN 'normal'
                    ELSE 'high'
                END AS bucket
            FROM TASK t
            WHERE t.proj_id = ?
              AND {schedule_task_type_condition("t")}
              AND t.status_code <> 'TK_Complete'
        ) buckets
        GROUP BY bucket
    """, (*critical_params, near_limit_hours, normal_limit_hours, project_id))
    dist = {"negative": 0, "critical": 0, "near_critical": 0, "normal": 0, "high": 0}
    for row in rows:
        dist[row["bucket"]] = row["count"]
    return dist


def get_progress_by_wbs(project_id: int) -> list[dict]:
    """Roll up schedule metrics by WBS node."""
    rows = query(f"""
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
        SELECT
            w.wbs_id,
            w.parent_wbs_id,
            w.wbs_short_name,
            w.wbs_name,
            wp.wbs_path,
            COUNT(*) AS total_activities,
            SUM(CASE WHEN t.task_type = 'TT_Task' THEN 1 ELSE 0 END) AS task_count,
            SUM(CASE WHEN t.task_type = 'TT_Rsrc' THEN 1 ELSE 0 END) AS resource_dependent_count,
            SUM(CASE WHEN t.task_type IN ('TT_Mile', 'TT_FinMile') THEN 1 ELSE 0 END) AS milestone_count,
            SUM(CASE WHEN t.status_code = 'TK_Complete' THEN 1 ELSE 0 END) AS complete,
            SUM(CASE WHEN t.status_code = 'TK_Active' THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN t.status_code = 'TK_NotStart' THEN 1 ELSE 0 END) AS not_started,
            ROUND(
                SUM(CASE WHEN t.status_code = 'TK_Complete' THEN 1.0 ELSE 0.0 END)
                / COUNT(*) * 100, 1
            ) AS percent_complete,
            SUM(CASE WHEN t.total_float_hr_cnt < 0 AND t.status_code <> 'TK_Complete'
                THEN 1 ELSE 0 END) AS negative_float_count
        FROM TASK t
        JOIN PROJWBS w ON t.wbs_id = w.wbs_id
        LEFT JOIN wbs_paths wp ON wp.wbs_id = w.wbs_id
        WHERE t.proj_id = ? AND {schedule_task_type_condition("t")}
        GROUP BY w.wbs_id, w.parent_wbs_id, w.wbs_short_name, w.wbs_name, wp.wbs_path
        ORDER BY
            SUM(CASE WHEN t.total_float_hr_cnt < 0 AND t.status_code <> 'TK_Complete'
                THEN 1 ELSE 0 END) DESC,
            COUNT(*) DESC,
            wp.wbs_path
    """, (project_id,))
    return rows

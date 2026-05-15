from db import query, query_single
from tools.common import (
    critical_condition,
    get_project_settings,
    schedule_task_type_condition,
)


def _limit(value: int, default: int = 50, maximum: int = 200) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _project(project_id: int) -> dict | None:
    return query_single("""
        SELECT
            proj_id,
            proj_short_name,
            description,
            CONVERT(varchar, plan_start_date, 23) AS plan_start_date,
            CONVERT(varchar, scd_end_date, 23) AS scd_end_date,
            CONVERT(varchar, last_recalc_date, 23) AS last_recalc_date
        FROM PROJECT
        WHERE proj_id = ?
    """, (project_id,))


def compare_schedule_updates(baseline_id: int, update_id: int, top_n: int = 50) -> dict:
    """Compare two P6 schedules with activity, logic, WBS, and path deltas.

    Args:
        baseline_id: The proj_id of the earlier schedule.
        update_id: The proj_id of the later/current schedule.
        top_n: Maximum rows returned per detail list.
    """
    top_n = _limit(top_n)
    baseline = _project(baseline_id)
    update = _project(update_id)
    if not baseline or not update:
        return {"error": "One or both project IDs were not found."}
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
            (SELECT COUNT(*) FROM TASK WHERE proj_id = ? AND {task_type_clause}) AS update_activities,
            (SELECT COUNT(*) FROM TASK WHERE proj_id = ? AND {task_type_clause} AND status_code <> 'TK_Complete') AS update_incomplete,
            (SELECT COUNT(*) FROM TASK WHERE proj_id = ? AND {task_type_clause} AND status_code = 'TK_Complete') AS update_complete
    """, (baseline_id, update_id, update_id, update_id))

    added = query(f"""
        SELECT TOP {top_n}
            u.task_code,
            u.task_name,
            u.task_type,
            w.wbs_name,
            u.status_code,
            CONVERT(varchar, u.target_start_date, 23) AS planned_start,
            CONVERT(varchar, u.target_end_date, 23) AS planned_finish,
            ROUND(u.target_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS planned_duration_days,
            ROUND(u.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days
        FROM TASK u
        LEFT JOIN PROJWBS w ON w.wbs_id = u.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = u.clndr_id
        WHERE u.proj_id = ?
          AND {schedule_task_type_condition("u")}
          AND NOT EXISTS (
              SELECT 1 FROM TASK b
              WHERE b.proj_id = ?
                AND {schedule_task_type_condition("b")}
                AND b.task_code = u.task_code
          )
        ORDER BY u.task_code
    """, (update_id, baseline_id))

    removed = query(f"""
        SELECT TOP {top_n}
            b.task_code,
            b.task_name,
            b.task_type,
            w.wbs_name,
            b.status_code,
            CONVERT(varchar, b.target_start_date, 23) AS planned_start,
            CONVERT(varchar, b.target_end_date, 23) AS planned_finish,
            ROUND(b.target_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS planned_duration_days,
            ROUND(b.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days
        FROM TASK b
        LEFT JOIN PROJWBS w ON w.wbs_id = b.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = b.clndr_id
        WHERE b.proj_id = ?
          AND {schedule_task_type_condition("b")}
          AND NOT EXISTS (
              SELECT 1 FROM TASK u
              WHERE u.proj_id = ?
                AND {schedule_task_type_condition("u")}
                AND u.task_code = b.task_code
          )
        ORDER BY b.task_code
    """, (baseline_id, update_id))

    changed_activities = query(f"""
        SELECT TOP {top_n}
            u.task_code,
            u.task_name,
            u.task_type,
            bw.wbs_name AS baseline_wbs,
            uw.wbs_name AS update_wbs,
            b.status_code AS baseline_status,
            u.status_code AS update_status,
            CONVERT(varchar, b.target_start_date, 23) AS baseline_start,
            CONVERT(varchar, u.target_start_date, 23) AS update_start,
            DATEDIFF(day, b.target_start_date, u.target_start_date) AS start_delta_days,
            CONVERT(varchar, b.target_end_date, 23) AS baseline_finish,
            CONVERT(varchar, u.target_end_date, 23) AS update_finish,
            DATEDIFF(day, b.target_end_date, u.target_end_date) AS finish_delta_days,
            (u.target_drtn_hr_cnt - b.target_drtn_hr_cnt) AS duration_delta_hours,
            (u.total_float_hr_cnt - b.total_float_hr_cnt) AS float_delta_hours,
            ROUND((u.target_drtn_hr_cnt - b.target_drtn_hr_cnt) / NULLIF(COALESCE(uc.day_hr_cnt, 8), 0), 1) AS duration_delta_days,
            ROUND((u.total_float_hr_cnt - b.total_float_hr_cnt) / NULLIF(COALESCE(uc.day_hr_cnt, 8), 0), 1) AS float_delta_days
        FROM TASK u
        JOIN TASK b ON b.proj_id = ?
            AND {schedule_task_type_condition("b")}
            AND b.task_code = u.task_code
        LEFT JOIN PROJWBS bw ON bw.wbs_id = b.wbs_id
        LEFT JOIN PROJWBS uw ON uw.wbs_id = u.wbs_id
        LEFT JOIN CALENDAR uc ON uc.clndr_id = u.clndr_id
        WHERE u.proj_id = ?
          AND {schedule_task_type_condition("u")}
          AND (
              ISNULL(CONVERT(varchar, b.target_start_date, 120), '') <> ISNULL(CONVERT(varchar, u.target_start_date, 120), '')
              OR ISNULL(CONVERT(varchar, b.target_end_date, 120), '') <> ISNULL(CONVERT(varchar, u.target_end_date, 120), '')
              OR ISNULL(b.target_drtn_hr_cnt, 0) <> ISNULL(u.target_drtn_hr_cnt, 0)
              OR ISNULL(b.total_float_hr_cnt, 0) <> ISNULL(u.total_float_hr_cnt, 0)
              OR ISNULL(b.status_code, '') <> ISNULL(u.status_code, '')
              OR ISNULL(bw.wbs_name, '') <> ISNULL(uw.wbs_name, '')
          )
        ORDER BY
            ABS(DATEDIFF(day, b.target_end_date, u.target_end_date)) DESC,
            ABS(u.total_float_hr_cnt - b.total_float_hr_cnt) DESC,
            u.task_code
    """, (baseline_id, update_id))

    logic_added = query(f"""
        SELECT TOP {top_n}
            us.task_code AS successor_code,
            us.task_name AS successor_name,
            up.task_code AS predecessor_code,
            up.task_name AS predecessor_name,
            ur.pred_type,
            ur.lag_hr_cnt AS lag_hours,
            ROUND(ur.lag_hr_cnt / 8.0, 1) AS lag_days_8h
        FROM TASKPRED ur
        JOIN TASK us ON us.task_id = ur.task_id AND us.proj_id = ?
        JOIN TASK up ON up.task_id = ur.pred_task_id
        WHERE NOT EXISTS (
            SELECT 1
            FROM TASKPRED br
            JOIN TASK bs ON bs.task_id = br.task_id AND bs.proj_id = ?
            JOIN TASK bp ON bp.task_id = br.pred_task_id
            WHERE bs.task_code = us.task_code
              AND bp.task_code = up.task_code
              AND ISNULL(br.pred_type, '') = ISNULL(ur.pred_type, '')
              AND ISNULL(br.lag_hr_cnt, 0) = ISNULL(ur.lag_hr_cnt, 0)
        )
        ORDER BY us.task_code, up.task_code
    """, (update_id, baseline_id))

    logic_removed = query(f"""
        SELECT TOP {top_n}
            bs.task_code AS successor_code,
            bs.task_name AS successor_name,
            bp.task_code AS predecessor_code,
            bp.task_name AS predecessor_name,
            br.pred_type,
            br.lag_hr_cnt AS lag_hours,
            ROUND(br.lag_hr_cnt / 8.0, 1) AS lag_days_8h
        FROM TASKPRED br
        JOIN TASK bs ON bs.task_id = br.task_id AND bs.proj_id = ?
        JOIN TASK bp ON bp.task_id = br.pred_task_id
        WHERE NOT EXISTS (
            SELECT 1
            FROM TASKPRED ur
            JOIN TASK us ON us.task_id = ur.task_id AND us.proj_id = ?
            JOIN TASK up ON up.task_id = ur.pred_task_id
            WHERE us.task_code = bs.task_code
              AND up.task_code = bp.task_code
              AND ISNULL(ur.pred_type, '') = ISNULL(br.pred_type, '')
              AND ISNULL(ur.lag_hr_cnt, 0) = ISNULL(br.lag_hr_cnt, 0)
        )
        ORDER BY bs.task_code, bp.task_code
    """, (baseline_id, update_id))

    baseline_settings = get_project_settings(baseline_id)
    update_settings = get_project_settings(update_id)
    baseline_critical_sql, baseline_critical_params, baseline_critical_basis, baseline_threshold = critical_condition(baseline_settings)
    update_critical_sql, update_critical_params, update_critical_basis, update_threshold = critical_condition(update_settings)
    baseline_critical = query(f"""
        SELECT t.task_code
        FROM TASK t
        WHERE t.proj_id = ?
          AND {schedule_task_type_condition("t")}
          AND t.status_code <> 'TK_Complete'
          AND {baseline_critical_sql}
    """, (baseline_id, *baseline_critical_params))
    update_critical = query(f"""
        SELECT t.task_code
        FROM TASK t
        WHERE t.proj_id = ?
          AND {schedule_task_type_condition("t")}
          AND t.status_code <> 'TK_Complete'
          AND {update_critical_sql}
    """, (update_id, *update_critical_params))
    baseline_crit_codes = {row["task_code"] for row in baseline_critical}
    update_crit_codes = {row["task_code"] for row in update_critical}

    wbs_variance = query(f"""
        WITH baseline_wbs AS (
            SELECT
                w.wbs_name,
                COUNT(*) AS activity_count,
                SUM(CASE WHEN t.status_code = 'TK_Complete' THEN 1 ELSE 0 END) AS complete_count,
                SUM(CASE WHEN t.status_code <> 'TK_Complete' AND t.total_float_hr_cnt < 0 THEN 1 ELSE 0 END) AS negative_float_count
            FROM TASK t
            LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
            WHERE t.proj_id = ? AND {schedule_task_type_condition("t")}
            GROUP BY w.wbs_name
        ),
        update_wbs AS (
            SELECT
                w.wbs_name,
                COUNT(*) AS activity_count,
                SUM(CASE WHEN t.status_code = 'TK_Complete' THEN 1 ELSE 0 END) AS complete_count,
                SUM(CASE WHEN t.status_code <> 'TK_Complete' AND t.total_float_hr_cnt < 0 THEN 1 ELSE 0 END) AS negative_float_count
            FROM TASK t
            LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
            WHERE t.proj_id = ? AND {schedule_task_type_condition("t")}
            GROUP BY w.wbs_name
        )
        SELECT TOP {top_n}
            COALESCE(u.wbs_name, b.wbs_name) AS wbs_name,
            ISNULL(b.activity_count, 0) AS baseline_activities,
            ISNULL(u.activity_count, 0) AS update_activities,
            ISNULL(u.activity_count, 0) - ISNULL(b.activity_count, 0) AS activity_delta,
            ISNULL(b.complete_count, 0) AS baseline_complete,
            ISNULL(u.complete_count, 0) AS update_complete,
            ISNULL(u.complete_count, 0) - ISNULL(b.complete_count, 0) AS complete_delta,
            ISNULL(b.negative_float_count, 0) AS baseline_negative_float,
            ISNULL(u.negative_float_count, 0) AS update_negative_float,
            ISNULL(u.negative_float_count, 0) - ISNULL(b.negative_float_count, 0) AS negative_float_delta
        FROM baseline_wbs b
        FULL OUTER JOIN update_wbs u ON ISNULL(u.wbs_name, '') = ISNULL(b.wbs_name, '')
        ORDER BY
            ABS(ISNULL(u.activity_count, 0) - ISNULL(b.activity_count, 0)) DESC,
            ABS(ISNULL(u.negative_float_count, 0) - ISNULL(b.negative_float_count, 0)) DESC,
            COALESCE(u.wbs_name, b.wbs_name)
    """, (baseline_id, update_id))

    return {
        "baseline": baseline,
        "update": update,
        "top_n_per_detail_list": top_n,
        "finish_delta_days": finish_delta["finish_delta_days"] if finish_delta else None,
        "activity_counts": counts,
        "added_activity_sample_count": len(added),
        "removed_activity_sample_count": len(removed),
        "changed_activity_sample_count": len(changed_activities),
        "logic_added_sample_count": len(logic_added),
        "logic_removed_sample_count": len(logic_removed),
        "critical_path_migration": {
            "baseline_critical_basis": baseline_critical_basis,
            "baseline_critical_threshold_hours": baseline_threshold,
            "update_critical_basis": update_critical_basis,
            "update_critical_threshold_hours": update_threshold,
            "baseline_critical_count": len(baseline_crit_codes),
            "update_critical_count": len(update_crit_codes),
            "became_critical": sorted(update_crit_codes - baseline_crit_codes)[:top_n],
            "no_longer_critical": sorted(baseline_crit_codes - update_crit_codes)[:top_n],
        },
        "added_activities": added,
        "removed_activities": removed,
        "changed_activities": changed_activities,
        "logic_added": logic_added,
        "logic_removed": logic_removed,
        "wbs_variance": wbs_variance,
    }

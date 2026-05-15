from db import query, query_single
from tools.common import schedule_task_type_condition


def _limit(value: int, default: int = 100, maximum: int = 500) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def get_project_calendars(project_id: int) -> dict:
    """Return calendars used by a project and activity counts per calendar.

    Args:
        project_id: The proj_id from get_project_list.
    """
    project = query_single("""
        SELECT
            p.proj_id,
            p.proj_short_name,
            p.clndr_id AS project_calendar_id,
            c.clndr_name AS project_calendar_name,
            ROUND(c.day_hr_cnt, 2) AS project_hours_per_day,
            ROUND(c.week_hr_cnt, 2) AS project_hours_per_week
        FROM PROJECT p
        LEFT JOIN CALENDAR c ON c.clndr_id = p.clndr_id
        WHERE p.proj_id = ?
    """, (project_id,))
    if not project:
        return {"error": f"Project {project_id} was not found."}

    activity_calendars = query(f"""
        SELECT
            c.clndr_id,
            c.clndr_name,
            c.clndr_type,
            c.default_flag,
            c.base_clndr_id,
            ROUND(c.day_hr_cnt, 2) AS hours_per_day,
            ROUND(c.week_hr_cnt, 2) AS hours_per_week,
            ROUND(c.month_hr_cnt, 2) AS hours_per_month,
            ROUND(c.year_hr_cnt, 2) AS hours_per_year,
            COUNT(*) AS activity_count,
            SUM(CASE WHEN t.status_code <> 'TK_Complete' THEN 1 ELSE 0 END) AS incomplete_activity_count,
            ROUND(SUM(ISNULL(t.target_drtn_hr_cnt, 0)) / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS planned_duration_days,
            ROUND(SUM(ISNULL(t.remain_drtn_hr_cnt, 0)) / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_duration_days
        FROM TASK t
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ?
          AND {schedule_task_type_condition("t")}
        GROUP BY
            c.clndr_id, c.clndr_name, c.clndr_type, c.default_flag,
            c.base_clndr_id, c.day_hr_cnt, c.week_hr_cnt, c.month_hr_cnt, c.year_hr_cnt
        ORDER BY activity_count DESC, c.clndr_name
    """, (project_id,))

    resource_calendars = query("""
        SELECT
            c.clndr_id,
            c.clndr_name,
            c.clndr_type,
            ROUND(c.day_hr_cnt, 2) AS hours_per_day,
            COUNT(DISTINCT tr.rsrc_id) AS resource_count,
            COUNT(*) AS assignment_count
        FROM TASKRSRC tr
        JOIN TASK t ON t.task_id = tr.task_id
        LEFT JOIN RSRC r ON r.rsrc_id = tr.rsrc_id
        LEFT JOIN CALENDAR c ON c.clndr_id = r.clndr_id
        WHERE tr.proj_id = ?
        GROUP BY c.clndr_id, c.clndr_name, c.clndr_type, c.day_hr_cnt
        HAVING COUNT(*) > 0
        ORDER BY assignment_count DESC, c.clndr_name
    """, (project_id,))

    return {
        "project": project,
        "activity_calendars": activity_calendars,
        "resource_calendars": resource_calendars,
    }


def get_calendar_detail(calendar_id: int, include_raw: bool = False) -> dict:
    """Return calendar metadata and usage.

    Args:
        calendar_id: P6 CALENDAR.clndr_id.
        include_raw: Include raw P6 calendar data text.
    """
    raw_col = ", CAST(clndr_data AS varchar(max)) AS raw_calendar_data" if include_raw else ""
    calendar = query_single(f"""
        SELECT
            clndr_id,
            clndr_name,
            clndr_type,
            default_flag,
            rsrc_private,
            proj_id,
            base_clndr_id,
            ROUND(day_hr_cnt, 2) AS hours_per_day,
            ROUND(week_hr_cnt, 2) AS hours_per_week,
            ROUND(month_hr_cnt, 2) AS hours_per_month,
            ROUND(year_hr_cnt, 2) AS hours_per_year,
            CONVERT(varchar, last_chng_date, 23) AS last_changed,
            DATALENGTH(CAST(clndr_data AS varchar(max))) AS raw_calendar_data_bytes
            {raw_col}
        FROM CALENDAR
        WHERE clndr_id = ?
    """, (calendar_id,))
    if not calendar:
        return {"error": f"Calendar {calendar_id} was not found."}

    project_usage = query("""
        SELECT TOP 50
            p.proj_id,
            p.proj_short_name,
            CONVERT(varchar, p.last_recalc_date, 23) AS data_date
        FROM PROJECT p
        WHERE p.clndr_id = ?
        ORDER BY p.proj_short_name
    """, (calendar_id,))

    activity_usage = query(f"""
        SELECT TOP 50
            p.proj_id,
            p.proj_short_name,
            COUNT(*) AS activity_count
        FROM TASK t
        JOIN PROJECT p ON p.proj_id = t.proj_id
        WHERE t.clndr_id = ?
          AND {schedule_task_type_condition("t")}
        GROUP BY p.proj_id, p.proj_short_name
        ORDER BY activity_count DESC, p.proj_short_name
    """, (calendar_id,))

    resource_usage = query("""
        SELECT TOP 50
            r.rsrc_id,
            r.rsrc_short_name,
            r.rsrc_name,
            r.active_flag
        FROM RSRC r
        WHERE r.clndr_id = ?
        ORDER BY r.rsrc_short_name
    """, (calendar_id,))

    return {
        "calendar": calendar,
        "project_usage": project_usage,
        "activity_usage": activity_usage,
        "resource_usage": resource_usage,
    }


def get_calendar_activity_usage(
    project_id: int,
    calendar_id: int | None = None,
    top_n: int = 100,
) -> list[dict]:
    """Return project activities with their assigned calendars.

    Args:
        project_id: The proj_id from get_project_list.
        calendar_id: Optional calendar id filter.
        top_n: Maximum activities returned.
    """
    top_n = _limit(top_n)
    calendar_clause = "AND t.clndr_id = ?" if calendar_id is not None else ""
    params = (project_id, int(calendar_id)) if calendar_id is not None else (project_id,)

    return query(f"""
        SELECT TOP {top_n}
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            w.wbs_name,
            t.clndr_id,
            c.clndr_name,
            c.clndr_type,
            ROUND(COALESCE(c.day_hr_cnt, 8), 2) AS hours_per_day,
            ROUND(t.target_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS planned_duration_calendar_days,
            t.target_drtn_hr_cnt AS planned_duration_hours,
            t.remain_drtn_hr_cnt AS remaining_duration_hours,
            t.total_float_hr_cnt AS total_float_hours,
            ROUND(t.target_drtn_hr_cnt / 8.0, 1) AS planned_duration_8h_days,
            ROUND(t.remain_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_duration_calendar_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_calendar_days,
            CONVERT(varchar, t.target_start_date, 23) AS planned_start,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish
        FROM TASK t
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ?
          AND {schedule_task_type_condition("t")}
          {calendar_clause}
        ORDER BY c.clndr_name, t.target_start_date, t.task_code
    """, params)

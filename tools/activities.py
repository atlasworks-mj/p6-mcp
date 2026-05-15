from db import query, query_single
from tools.common import (
    critical_condition,
    get_project_settings,
    project_hours_per_day,
    schedule_task_type_condition,
)


def get_activities(
    project_id: int,
    status: str = "all",
    wbs_filter: str = None,
    float_range: str = "all",
    sort_by: str = "start_date",
    top_n: int = 50,
) -> list[dict]:
    """Return activities for a project with flexible filtering and sorting.

    Args:
        project_id: The proj_id from get_project_list.
        status: Filter by completion — "all", "incomplete", "complete", or "in_progress".
        wbs_filter: Filter by WBS name using a LIKE match (e.g. "Structural" matches
                     any WBS containing that word). None means no WBS filter.
        float_range: Filter by total float —
                     "all"           no filter
                     "negative"      float < 0
                     "critical"      float = 0
                     "near_critical" float 0–10 workdays (0–80 hrs)
                     "high"          float > 44 workdays (> 352 hrs)
        sort_by: Column to ORDER BY —
                 "start_date" (default), "float", or "duration".
        top_n: Maximum rows returned (default 50). Keeps LLM context manageable.
    """
    project = get_project_settings(project_id)
    clauses = []
    params = [project_id]

    # --- status filter ---
    if status == "incomplete":
        clauses.append("AND t.status_code != 'TK_Complete'")
    elif status == "complete":
        clauses.append("AND t.status_code = 'TK_Complete'")
    elif status == "in_progress":
        clauses.append("AND t.status_code = 'TK_Active'")

    # --- WBS filter (requires JOIN) ---
    wbs_join = ""
    if wbs_filter:
        wbs_join = "JOIN PROJWBS w ON t.wbs_id = w.wbs_id"
        clauses.append("AND w.wbs_name LIKE ?")
        params.append(f"%{wbs_filter}%")

    # --- float range filter ---
    if float_range == "negative":
        clauses.append("AND t.total_float_hr_cnt < 0")
    elif float_range == "critical":
        critical_sql, critical_params, _, _ = critical_condition(project)
        clauses.append(f"AND {critical_sql}")
        params.extend(critical_params)
    elif float_range == "zero_float":
        clauses.append("AND t.total_float_hr_cnt = 0")
    elif float_range == "near_critical":
        clauses.append("AND t.total_float_hr_cnt >= 0 AND t.total_float_hr_cnt <= ?")
        params.append(project_hours_per_day(project) * 10)
    elif float_range == "high":
        clauses.append("AND t.total_float_hr_cnt > ?")
        params.append(project_hours_per_day(project) * 44)

    # --- sort ---
    sort_map = {
        "start_date": "t.target_start_date",
        "float": "t.total_float_hr_cnt",
        "duration": "t.target_drtn_hr_cnt DESC",
    }
    order_col = sort_map.get(sort_by, "t.target_start_date")

    where_extra = "\n        ".join(clauses)

    # Build optional WBS column for the SELECT list
    wbs_col = ",\n            w.wbs_name" if wbs_filter else ""

    top_n = max(1, min(int(top_n), 500))

    sql = f"""
        SELECT TOP {top_n}
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            CONVERT(varchar, t.target_start_date, 23) AS target_start,
            CONVERT(varchar, t.target_end_date, 23)   AS target_end,
            CONVERT(varchar, t.act_start_date, 23)     AS actual_start,
            CONVERT(varchar, t.act_end_date, 23)       AS actual_end,
            t.target_drtn_hr_cnt AS duration_hours,
            t.total_float_hr_cnt AS total_float_hours,
            ROUND(COALESCE(c.day_hr_cnt, 8), 2) AS calendar_hours_per_day,
            ROUND(t.target_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS duration_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days{wbs_col}
        FROM TASK t
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        {wbs_join}
        WHERE t.proj_id = ? AND {schedule_task_type_condition("t")}
        {where_extra}
        ORDER BY {order_col}
    """
    return query(sql, tuple(params))


def get_critical_path(project_id: int) -> dict:
    """Return critical activities using the project's P6 critical path setting.

    If PROJECT.critical_path_type is CT_DrivPath, the tool uses P6's calculated
    TASK.driving_path_flag. If it is CT_TotFloat, the tool uses the project's
    configured total-float threshold in PROJECT.critical_drtn_hr_cnt.

    Args:
        project_id: The proj_id from get_project_list.
    """
    project = get_project_settings(project_id)
    if not project:
        return {"error": f"Project {project_id} was not found."}

    critical_sql, critical_params, basis, threshold_hours = critical_condition(project)
    params = (project_id, *critical_params)

    rows = query(f"""
        SELECT
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            w.wbs_name,
            t.clndr_id,
            c.clndr_name,
            ROUND(COALESCE(c.day_hr_cnt, 8), 2) AS calendar_hours_per_day,
            CONVERT(varchar, t.target_start_date, 23) AS target_start,
            CONVERT(varchar, t.target_end_date, 23)   AS target_end,
            CONVERT(varchar, t.early_start_date, 23) AS early_start,
            CONVERT(varchar, t.early_end_date, 23) AS early_finish,
            t.target_drtn_hr_cnt AS duration_hours,
            t.remain_drtn_hr_cnt AS remaining_duration_hours,
            t.total_float_hr_cnt AS total_float_hours,
            t.free_float_hr_cnt AS free_float_hours,
            ROUND(t.target_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS duration_days,
            ROUND(t.remain_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_duration_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days,
            ROUND(t.free_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS free_float_days,
            t.driving_path_flag,
            t.float_path,
            t.float_path_order
        FROM TASK t
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ?
          AND {schedule_task_type_condition("t")}
          AND t.status_code != 'TK_Complete'
          AND {critical_sql}
        ORDER BY
            COALESCE(t.float_path, 999999),
            COALESCE(t.float_path_order, 999999),
            t.early_start_date,
            t.target_start_date,
            t.task_code
    """, params)

    hours_per_day = project_hours_per_day(project)
    threshold_days = None if threshold_hours is None else round(threshold_hours / hours_per_day, 2)
    return {
        "project": project,
        "critical_basis": basis,
        "critical_path_type": project.get("critical_path_type"),
        "critical_threshold_hours": threshold_hours,
        "critical_threshold_days": threshold_days,
        "critical_threshold_defaulted": (
            project.get("critical_path_type") != "CT_DrivPath"
            and project.get("critical_drtn_hr_cnt") is None
        ),
        "activity_count": len(rows),
        "critical_activities": rows,
    }


def get_slippage(project_id: int) -> list[dict]:
    """Return completed activities where actual finish exceeded the target finish.

    Useful for schedule forensics — shows which activities slipped and by how
    many workdays (assuming 8-hr days).  Sorted worst slippage first.

    Args:
        project_id: The proj_id from get_project_list.

    Returns:
        List of dicts with task_code, task_name, planned_finish, actual_finish,
        and slip_days (positive = late).
    """
    return query(f"""
        SELECT
            t.task_code,
            t.task_name,
            t.task_type,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            CONVERT(varchar, t.act_end_date, 23)    AS actual_finish,
            DATEDIFF(day, t.target_end_date, t.act_end_date) AS slip_days
        FROM TASK t
        WHERE t.proj_id = ?
          AND {schedule_task_type_condition("t")}
          AND t.status_code = 'TK_Complete'
          AND t.act_end_date > t.target_end_date
        ORDER BY slip_days DESC
    """, (project_id,))


def get_procurement_status(project_id: int) -> list[dict]:
    """Return procurement / buyout activities sorted by float ascending (worst first).

    Detection logic: task_code starts with 'BUY_' OR the parent WBS name
    contains 'Buyout' or 'Procurement'.

    Args:
        project_id: The proj_id from get_project_list.

    Returns:
        List of dicts with task_code, task_name, status_code,
        remaining_duration_days, total_float_days, and planned_finish.
    """
    return query("""
        SELECT
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            t.remain_drtn_hr_cnt AS remaining_duration_hours,
            t.total_float_hr_cnt AS total_float_hours,
            ROUND(COALESCE(c.day_hr_cnt, 8), 2) AS calendar_hours_per_day,
            ROUND(t.remain_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_duration_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days,
            CONVERT(varchar, t.target_end_date, 23)     AS planned_finish
        FROM TASK t
        JOIN PROJWBS w ON t.wbs_id = w.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ?
          AND t.task_type = 'TT_Task'
          AND (
              t.task_code LIKE 'BUY_%'
              OR w.wbs_name LIKE '%Buyout%'
              OR w.wbs_name LIKE '%Procurement%'
          )
        ORDER BY t.total_float_hr_cnt ASC
    """, (project_id,))

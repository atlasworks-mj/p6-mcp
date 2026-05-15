from db import query_single


SCHEDULE_TASK_TYPES = ("TT_Task", "TT_Rsrc", "TT_Mile", "TT_FinMile")
SCHEDULE_TASK_TYPE_LIST = "'TT_Task', 'TT_Rsrc', 'TT_Mile', 'TT_FinMile'"


def schedule_task_type_condition(alias: str = "t") -> str:
    """Return the SQL condition for real schedule activities and milestones."""
    prefix = f"{alias}." if alias else ""
    return f"{prefix}task_type IN ({SCHEDULE_TASK_TYPE_LIST})"


def get_project_settings(project_id: int) -> dict | None:
    """Return project schedule settings used by multiple tools."""
    return query_single("""
        SELECT
            p.proj_id,
            p.proj_short_name,
            p.critical_path_type,
            p.critical_drtn_hr_cnt,
            p.clndr_id AS project_calendar_id,
            c.clndr_name AS project_calendar_name,
            ROUND(COALESCE(c.day_hr_cnt, 8), 2) AS project_hours_per_day,
            CONVERT(varchar, p.last_recalc_date, 120) AS last_recalc_date
        FROM PROJECT p
        LEFT JOIN CALENDAR c ON c.clndr_id = p.clndr_id
        WHERE p.proj_id = ?
    """, (project_id,))


def project_hours_per_day(project: dict | None) -> float:
    try:
        hours = float((project or {}).get("project_hours_per_day") or 8)
        return hours if hours > 0 else 8.0
    except (TypeError, ValueError):
        return 8.0


def critical_condition(project: dict | None, alias: str = "t") -> tuple[str, tuple, str, float]:
    """Return SQL predicate/params matching the project's P6 critical setting."""
    prefix = f"{alias}." if alias else ""
    project = project or {}
    if project.get("critical_path_type") == "CT_DrivPath":
        return f"{prefix}driving_path_flag = 'Y'", (), "p6_driving_path_flag", 0.0

    threshold_hours = project.get("critical_drtn_hr_cnt")
    try:
        threshold_hours = float(threshold_hours if threshold_hours is not None else 0)
    except (TypeError, ValueError):
        threshold_hours = 0.0
    return f"{prefix}total_float_hr_cnt <= ?", (threshold_hours,), "p6_total_float_threshold", threshold_hours


def critical_summary(project: dict | None) -> dict:
    condition, _, basis, threshold_hours = critical_condition(project)
    hours_per_day = project_hours_per_day(project)
    return {
        "critical_basis": basis,
        "critical_path_type": (project or {}).get("critical_path_type"),
        "critical_threshold_hours": threshold_hours,
        "critical_threshold_days": round(threshold_hours / hours_per_day, 2),
        "critical_condition": condition,
    }

from db import query
from tools.common import schedule_task_type_condition


def get_wbs(project_id: int) -> list[dict]:
    """Return the WBS hierarchy for a project.

    Args:
        project_id: The proj_id from get_project_list.
    """
    return query(f"""
        SELECT
            wbs_id,
            parent_wbs_id,
            wbs_short_name,
            wbs_name,
            (SELECT COUNT(*) FROM TASK t WHERE t.wbs_id = w.wbs_id AND {schedule_task_type_condition("t")}) AS activity_count,
            (SELECT COUNT(*) FROM TASK t WHERE t.wbs_id = w.wbs_id AND t.task_type IN ('TT_Mile', 'TT_FinMile')) AS milestone_count
        FROM PROJWBS w
        WHERE w.proj_id = ?
        ORDER BY wbs_short_name
    """, (project_id,))

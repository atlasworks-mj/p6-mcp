from db import query


def get_project_list() -> list[dict]:
    """Return all projects in the P6 database with key metadata."""
    return query("""
        SELECT
            proj_id,
            proj_short_name,
            description,
            CONVERT(varchar, plan_start_date, 23) AS plan_start_date,
            CONVERT(varchar, scd_end_date, 23) AS scd_end_date,
            CONVERT(varchar, last_recalc_date, 23) AS last_recalc_date
        FROM PROJECT
        WHERE project_flag = 'Y'
        ORDER BY proj_short_name
    """)

from db import query, query_single


def _limit(value: int, default: int = 50, maximum: int = 200) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def get_resource_summary(project_id: int, group_by: str = "resource", top_n: int = 50) -> list[dict]:
    """Summarize labor/resource assignments by resource, role, WBS, or type.

    Args:
        project_id: The proj_id from get_project_list.
        group_by: One of "resource", "role", "wbs", or "type".
        top_n: Maximum groups returned.
    """
    top_n = _limit(top_n)
    group_by = (group_by or "resource").lower()
    group_map = {
        "resource": (
            "COALESCE(r.rsrc_short_name, ro.role_short_name, tr.rsrc_type, '(unassigned)')",
            "MAX(COALESCE(r.rsrc_name, ro.role_name, tr.rsrc_type, '(unassigned)'))",
        ),
        "role": (
            "COALESCE(ro.role_short_name, '(no role)')",
            "MAX(COALESCE(ro.role_name, '(no role)'))",
        ),
        "wbs": (
            "COALESCE(w.wbs_name, '(no WBS)')",
            "MAX(COALESCE(w.wbs_name, '(no WBS)'))",
        ),
        "type": (
            "COALESCE(tr.rsrc_type, '(unknown type)')",
            "MAX(COALESCE(tr.rsrc_type, '(unknown type)'))",
        ),
    }
    key_expr, name_expr = group_map.get(group_by, group_map["resource"])

    return query(f"""
        SELECT TOP {top_n}
            {key_expr} AS group_key,
            {name_expr} AS group_name,
            COUNT(DISTINCT tr.task_id) AS assigned_activity_count,
            COUNT(*) AS assignment_count,
            ROUND(SUM(ISNULL(tr.target_qty, 0)), 2) AS target_qty,
            ROUND(SUM(ISNULL(tr.act_reg_qty, 0) + ISNULL(tr.act_ot_qty, 0)), 2) AS actual_qty,
            ROUND(SUM(ISNULL(tr.remain_qty, 0)), 2) AS remaining_qty,
            ROUND(SUM(ISNULL(tr.target_cost, 0)), 2) AS target_cost,
            ROUND(SUM(ISNULL(tr.act_reg_cost, 0) + ISNULL(tr.act_ot_cost, 0)), 2) AS actual_cost,
            ROUND(SUM(ISNULL(tr.remain_cost, 0)), 2) AS remaining_cost
        FROM TASKRSRC tr
        LEFT JOIN TASK t ON t.task_id = tr.task_id
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN RSRC r ON r.rsrc_id = tr.rsrc_id
        LEFT JOIN ROLES ro ON ro.role_id = tr.role_id
        WHERE tr.proj_id = ?
        GROUP BY {key_expr}
        ORDER BY SUM(ISNULL(tr.target_cost, 0) + ISNULL(tr.remain_cost, 0)) DESC, group_key
    """, (project_id,))


def get_cost_summary(project_id: int, top_n: int = 50) -> dict:
    """Return project cost totals and WBS-level cost rollups.

    Includes both resource assignment costs (TASKRSRC) and project cost items
    (PROJCOST) where available.

    Args:
        project_id: The proj_id from get_project_list.
        top_n: Maximum WBS/detail rows returned.
    """
    top_n = _limit(top_n)
    project = query_single("""
        SELECT proj_id, proj_short_name
        FROM PROJECT
        WHERE proj_id = ?
    """, (project_id,))
    if not project:
        return {"error": f"Project {project_id} was not found."}

    resource_totals = query_single("""
        SELECT
            COUNT(*) AS assignment_count,
            ROUND(SUM(ISNULL(target_cost, 0)), 2) AS target_cost,
            ROUND(SUM(ISNULL(act_reg_cost, 0) + ISNULL(act_ot_cost, 0)), 2) AS actual_cost,
            ROUND(SUM(ISNULL(remain_cost, 0)), 2) AS remaining_cost
        FROM TASKRSRC
        WHERE proj_id = ?
    """, (project_id,))

    expense_totals = query_single("""
        SELECT
            COUNT(*) AS cost_item_count,
            ROUND(SUM(ISNULL(target_cost, 0)), 2) AS target_cost,
            ROUND(SUM(ISNULL(act_cost, 0)), 2) AS actual_cost,
            ROUND(SUM(ISNULL(remain_cost, 0)), 2) AS remaining_cost
        FROM PROJCOST
        WHERE proj_id = ?
    """, (project_id,))

    by_wbs = query(f"""
        SELECT TOP {top_n}
            cost_source,
            wbs_name,
            COUNT(*) AS row_count,
            ROUND(SUM(target_cost), 2) AS target_cost,
            ROUND(SUM(actual_cost), 2) AS actual_cost,
            ROUND(SUM(remaining_cost), 2) AS remaining_cost
        FROM (
            SELECT
                'resource_assignment' AS cost_source,
                COALESCE(w.wbs_name, '(no WBS)') AS wbs_name,
                ISNULL(tr.target_cost, 0) AS target_cost,
                ISNULL(tr.act_reg_cost, 0) + ISNULL(tr.act_ot_cost, 0) AS actual_cost,
                ISNULL(tr.remain_cost, 0) AS remaining_cost
            FROM TASKRSRC tr
            LEFT JOIN TASK t ON t.task_id = tr.task_id
            LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
            WHERE tr.proj_id = ?

            UNION ALL

            SELECT
                'cost_item' AS cost_source,
                COALESCE(w.wbs_name, '(no WBS)') AS wbs_name,
                ISNULL(pc.target_cost, 0) AS target_cost,
                ISNULL(pc.act_cost, 0) AS actual_cost,
                ISNULL(pc.remain_cost, 0) AS remaining_cost
            FROM PROJCOST pc
            LEFT JOIN TASK t ON t.task_id = pc.task_id
            LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
            WHERE pc.proj_id = ?
        ) costs
        GROUP BY cost_source, wbs_name
        ORDER BY SUM(target_cost + remaining_cost) DESC, wbs_name
    """, (project_id, project_id))

    cost_items = query(f"""
        SELECT TOP {top_n}
            pc.cost_name,
            pc.vendor_name,
            pc.po_number,
            t.task_code,
            t.task_name,
            w.wbs_name,
            ROUND(pc.target_cost, 2) AS target_cost,
            ROUND(pc.act_cost, 2) AS actual_cost,
            ROUND(pc.remain_cost, 2) AS remaining_cost
        FROM PROJCOST pc
        LEFT JOIN TASK t ON t.task_id = pc.task_id
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        WHERE pc.proj_id = ?
        ORDER BY ISNULL(pc.target_cost, 0) + ISNULL(pc.remain_cost, 0) DESC, pc.cost_name
    """, (project_id,))

    return {
        "project": project,
        "resource_totals": resource_totals,
        "expense_totals": expense_totals,
        "cost_by_wbs": by_wbs,
        "cost_items": cost_items,
    }


def get_cash_flow_by_month(
    project_id: int,
    date_basis: str = "planned_finish",
    source: str = "all",
) -> list[dict]:
    """Bucket resource and/or cost-item cost by month.

    This is a coarse monthly bucket based on assignment or cost item dates. It
    does not parse P6 spread curves.

    Args:
        project_id: The proj_id from get_project_list.
        date_basis: "planned_start", "planned_finish", or "actual".
        source: "all", "resource", or "expense".
    """
    date_basis = (date_basis or "planned_finish").lower()
    source = (source or "all").lower()

    resource_date = {
        "planned_start": "COALESCE(tr.target_start_date, t.target_start_date)",
        "planned_finish": "COALESCE(tr.target_end_date, t.target_end_date)",
        "actual": "COALESCE(tr.act_end_date, tr.act_start_date, t.act_end_date, t.act_start_date)",
    }.get(date_basis, "COALESCE(tr.target_end_date, t.target_end_date)")

    expense_date = {
        "planned_start": "t.target_start_date",
        "planned_finish": "t.target_end_date",
        "actual": "COALESCE(t.act_end_date, t.act_start_date)",
    }.get(date_basis, "t.target_end_date")

    subqueries = []
    params: list[int] = []
    if source in {"all", "resource"}:
        subqueries.append(f"""
            SELECT
                'resource' AS source_type,
                CONVERT(varchar(7), {resource_date}, 120) AS period_month,
                ISNULL(tr.target_cost, 0) AS target_cost,
                ISNULL(tr.act_reg_cost, 0) + ISNULL(tr.act_ot_cost, 0) AS actual_cost,
                ISNULL(tr.remain_cost, 0) AS remaining_cost
            FROM TASKRSRC tr
            LEFT JOIN TASK t ON t.task_id = tr.task_id
            WHERE tr.proj_id = ?
        """)
        params.append(project_id)
    if source in {"all", "expense"}:
        subqueries.append(f"""
            SELECT
                'expense' AS source_type,
                CONVERT(varchar(7), {expense_date}, 120) AS period_month,
                ISNULL(pc.target_cost, 0) AS target_cost,
                ISNULL(pc.act_cost, 0) AS actual_cost,
                ISNULL(pc.remain_cost, 0) AS remaining_cost
            FROM PROJCOST pc
            LEFT JOIN TASK t ON t.task_id = pc.task_id
            WHERE pc.proj_id = ?
        """)
        params.append(project_id)

    if not subqueries:
        return [{"error": "source must be one of: all, resource, expense"}]

    union_sql = "\nUNION ALL\n".join(subqueries)
    return query(f"""
        SELECT
            period_month,
            source_type,
            ROUND(SUM(target_cost), 2) AS target_cost,
            ROUND(SUM(actual_cost), 2) AS actual_cost,
            ROUND(SUM(remaining_cost), 2) AS remaining_cost,
            ROUND(SUM(actual_cost + remaining_cost), 2) AS forecast_cost
        FROM (
            {union_sql}
        ) cost_spread
        WHERE period_month IS NOT NULL
        GROUP BY period_month, source_type
        ORDER BY period_month, source_type
    """, tuple(params))

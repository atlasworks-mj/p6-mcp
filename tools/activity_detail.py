from db import query


def _activity_candidates(project_id: int, task_code: str) -> list[dict]:
    return query("""
        SELECT TOP 10
            t.task_code,
            t.task_name,
            t.status_code,
            w.wbs_name
        FROM TASK t
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        WHERE t.proj_id = ?
          AND t.task_code LIKE ?
        ORDER BY t.task_code
    """, (project_id, f"%{task_code}%"))


def get_activity_detail(project_id: int, task_code: str) -> dict:
    """Return full activity detail, logic, resources, costs, notes, and UDFs.

    Args:
        project_id: The proj_id from get_project_list.
        task_code: Exact P6 activity ID/code.
    """
    rows = query("""
        SELECT
            t.task_id,
            t.proj_id,
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            t.duration_type,
            t.complete_pct_type,
            ROUND(t.phys_complete_pct, 2) AS physical_percent_complete,
            t.wbs_id,
            w.wbs_short_name,
            w.wbs_name,
            t.clndr_id,
            c.clndr_name,
            c.clndr_type,
            ROUND(COALESCE(c.day_hr_cnt, 8), 2) AS calendar_hours_per_day,
            t.target_drtn_hr_cnt AS planned_duration_hours,
            t.remain_drtn_hr_cnt AS remaining_duration_hours,
            t.total_float_hr_cnt AS total_float_hours,
            t.free_float_hr_cnt AS free_float_hours,
            ROUND(t.target_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS planned_duration_days,
            ROUND(t.remain_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_duration_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days,
            ROUND(t.free_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS free_float_days,
            ROUND(t.target_work_qty, 2) AS target_work_qty,
            ROUND(t.act_work_qty, 2) AS actual_work_qty,
            ROUND(t.remain_work_qty, 2) AS remaining_work_qty,
            CONVERT(varchar, t.target_start_date, 23) AS planned_start,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            CONVERT(varchar, t.early_start_date, 23) AS early_start,
            CONVERT(varchar, t.early_end_date, 23) AS early_finish,
            CONVERT(varchar, t.late_start_date, 23) AS late_start,
            CONVERT(varchar, t.late_end_date, 23) AS late_finish,
            CONVERT(varchar, t.act_start_date, 23) AS actual_start,
            CONVERT(varchar, t.act_end_date, 23) AS actual_finish,
            CONVERT(varchar, t.restart_date, 23) AS remaining_start,
            CONVERT(varchar, t.reend_date, 23) AS remaining_finish,
            t.cstr_type,
            CONVERT(varchar, t.cstr_date, 23) AS constraint_date,
            t.cstr_type2,
            CONVERT(varchar, t.cstr_date2, 23) AS constraint_date2,
            t.driving_path_flag,
            t.float_path,
            t.float_path_order,
            CONVERT(varchar, t.update_date, 23) AS update_date,
            t.update_user
        FROM TASK t
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ?
          AND t.task_code = ?
    """, (project_id, task_code))

    if not rows:
        return {
            "error": f"Activity '{task_code}' was not found in project_id {project_id}.",
            "candidates": _activity_candidates(project_id, task_code),
        }

    activity = rows[0]
    task_id = activity["task_id"]

    predecessors = query("""
        SELECT
            p.proj_id AS predecessor_project_id,
            p.task_code AS predecessor_code,
            p.task_name AS predecessor_name,
            p.status_code AS predecessor_status,
            tp.pred_type,
            tp.lag_hr_cnt AS lag_hours,
            ROUND(tp.lag_hr_cnt / NULLIF(COALESCE(pc.day_hr_cnt, 8), 0), 1) AS lag_days,
            ROUND(p.total_float_hr_cnt / NULLIF(COALESCE(pc.day_hr_cnt, 8), 0), 1) AS predecessor_float_days,
            CONVERT(varchar, p.target_start_date, 23) AS predecessor_start,
            CONVERT(varchar, p.target_end_date, 23) AS predecessor_finish,
            CONVERT(varchar, p.act_start_date, 23) AS predecessor_actual_start,
            CONVERT(varchar, p.act_end_date, 23) AS predecessor_actual_finish
        FROM TASKPRED tp
        JOIN TASK p ON p.task_id = tp.pred_task_id
        LEFT JOIN CALENDAR pc ON pc.clndr_id = p.clndr_id
        WHERE tp.task_id = ?
        ORDER BY p.target_end_date, p.task_code
    """, (task_id,))

    successors = query("""
        SELECT
            s.proj_id AS successor_project_id,
            s.task_code AS successor_code,
            s.task_name AS successor_name,
            s.status_code AS successor_status,
            tp.pred_type,
            tp.lag_hr_cnt AS lag_hours,
            ROUND(tp.lag_hr_cnt / NULLIF(COALESCE(sc.day_hr_cnt, 8), 0), 1) AS lag_days,
            ROUND(s.total_float_hr_cnt / NULLIF(COALESCE(sc.day_hr_cnt, 8), 0), 1) AS successor_float_days,
            CONVERT(varchar, s.target_start_date, 23) AS successor_start,
            CONVERT(varchar, s.target_end_date, 23) AS successor_finish,
            CONVERT(varchar, s.act_start_date, 23) AS successor_actual_start,
            CONVERT(varchar, s.act_end_date, 23) AS successor_actual_finish
        FROM TASKPRED tp
        JOIN TASK s ON s.task_id = tp.task_id
        LEFT JOIN CALENDAR sc ON sc.clndr_id = s.clndr_id
        WHERE tp.pred_task_id = ?
        ORDER BY s.target_start_date, s.task_code
    """, (task_id,))

    resources = query("""
        SELECT
            tr.taskrsrc_id,
            tr.rsrc_type,
            r.rsrc_short_name,
            r.rsrc_name,
            ro.role_short_name,
            ro.role_name,
            ROUND(tr.target_qty, 2) AS target_qty,
            ROUND(tr.act_reg_qty + tr.act_ot_qty, 2) AS actual_qty,
            ROUND(tr.remain_qty, 2) AS remaining_qty,
            ROUND(tr.target_cost, 2) AS target_cost,
            ROUND(tr.act_reg_cost + tr.act_ot_cost, 2) AS actual_cost,
            ROUND(tr.remain_cost, 2) AS remaining_cost,
            CONVERT(varchar, tr.target_start_date, 23) AS planned_start,
            CONVERT(varchar, tr.target_end_date, 23) AS planned_finish
        FROM TASKRSRC tr
        LEFT JOIN RSRC r ON r.rsrc_id = tr.rsrc_id
        LEFT JOIN ROLES ro ON ro.role_id = tr.role_id
        WHERE tr.task_id = ?
        ORDER BY COALESCE(r.rsrc_short_name, ro.role_short_name, tr.rsrc_type)
    """, (task_id,))

    cost_items = query("""
        SELECT
            cost_item_id,
            cost_name,
            vendor_name,
            po_number,
            qty_name,
            ROUND(target_qty, 2) AS target_qty,
            ROUND(target_cost, 2) AS target_cost,
            ROUND(act_cost, 2) AS actual_cost,
            ROUND(remain_cost, 2) AS remaining_cost
        FROM PROJCOST
        WHERE proj_id = ?
          AND task_id = ?
        ORDER BY cost_name
    """, (project_id, task_id))

    notes = query("""
        SELECT CAST(task_notes AS varchar(max)) AS task_notes
        FROM TASKNOTE
        WHERE proj_id = ?
          AND task_id = ?
    """, (project_id, task_id))

    memos = query("""
        SELECT
            mt.memo_type,
            CAST(tm.task_memo AS varchar(max)) AS task_memo
        FROM TASKMEMO tm
        LEFT JOIN MEMOTYPE mt ON mt.memo_type_id = tm.memo_type_id
        WHERE tm.proj_id = ?
          AND tm.task_id = ?
        ORDER BY mt.memo_type
    """, (project_id, task_id))

    udfs = query("""
        SELECT
            ut.udf_type_name,
            ut.udf_type_label,
            ut.table_name AS udf_type_table_name,
            uv.table_name AS udf_value_table_name,
            COALESCE(uv.table_name, ut.table_name) AS resolved_table_name,
            ut.logical_data_type,
            CONVERT(varchar, uv.udf_date, 23) AS udf_date,
            uv.udf_text,
            uv.udf_number,
            uc.short_name AS udf_code_short_name,
            uc.udf_code_name
        FROM UDFVALUE uv
        JOIN UDFTYPE ut ON ut.udf_type_id = uv.udf_type_id
        LEFT JOIN UDFCODE uc ON uc.udf_code_id = uv.udf_code_id
        WHERE COALESCE(uv.table_name, ut.table_name) = 'TASK'
          AND uv.proj_id = ?
          AND uv.fk_id = ?
        ORDER BY ut.udf_type_name
    """, (project_id, task_id))

    return {
        "activity": activity,
        "predecessor_count": len(predecessors),
        "successor_count": len(successors),
        "predecessors": predecessors,
        "successors": successors,
        "resources": resources,
        "cost_items": cost_items,
        "notes": notes,
        "memos": memos,
        "udfs": udfs,
    }

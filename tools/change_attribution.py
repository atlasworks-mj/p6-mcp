from db import query, query_single
from tools.common import get_project_settings, project_hours_per_day, schedule_task_type_condition


VALID_GROUP_BY = {"wbs", "activity_code", "resource", "role"}
VALID_CODE_SCOPES = {
    "global": "AS_Global",
    "as_global": "AS_Global",
    "project": "AS_Project",
    "as_project": "AS_Project",
}


def _limit(value: int, default: int = 50, maximum: int = 200) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _like(value: str) -> str:
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("[", "\\[")
    )
    return f"%{escaped}%"


def _code_scope_filter(alias: str, code_scope: str | None, warnings: list[str]) -> tuple[str, str | None]:
    if not code_scope or str(code_scope).strip().lower() in {"all", "any", "*"}:
        return "", None

    normalized = str(code_scope).strip().lower().replace("-", "_")
    p6_scope = VALID_CODE_SCOPES.get(normalized)
    if not p6_scope:
        warnings.append(
            "Invalid code_scope was ignored. Use 'global'/'AS_Global', 'project'/'AS_Project', or omit it."
        )
        return "", None
    return f" AND {alias}.actv_code_type_scope = '{p6_scope}'", p6_scope


def _project(project_id: int) -> dict | None:
    row = query_single("""
        SELECT
            p.proj_id,
            p.proj_short_name,
            p.description,
            p.project_flag,
            p.orig_proj_id,
            p.sum_base_proj_id,
            p.base_type_id,
            bt.base_type,
            CONVERT(varchar, p.plan_start_date, 23) AS plan_start_date,
            CONVERT(varchar, p.scd_end_date, 23) AS scd_end_date,
            CONVERT(varchar, p.last_recalc_date, 23) AS last_recalc_date,
            CONVERT(varchar, p.last_schedule_date, 23) AS last_schedule_date,
            CONVERT(varchar, p.update_date, 23) AS update_date,
            w.wbs_name AS project_name
        FROM PROJECT p
        LEFT JOIN BASETYPE bt ON bt.base_type_id = p.base_type_id
        LEFT JOIN PROJWBS w ON w.proj_id = p.proj_id AND w.proj_node_flag = 'Y'
        WHERE p.proj_id = ?
    """, (project_id,))
    settings = get_project_settings(project_id)
    if row and settings:
        row["project_calendar_id"] = settings.get("project_calendar_id")
        row["project_calendar_name"] = settings.get("project_calendar_name")
        row["project_hours_per_day"] = settings.get("project_hours_per_day")
        row["critical_path_type"] = settings.get("critical_path_type")
        row["critical_drtn_hr_cnt"] = settings.get("critical_drtn_hr_cnt")
    return row


def _totals(baseline_id: int, update_id: int) -> dict:
    return query_single(f"""
        SELECT
            (SELECT COUNT(*) FROM TASK b WHERE b.proj_id = ? AND {schedule_task_type_condition("b")}) AS baseline_activity_count,
            (SELECT COUNT(*) FROM TASK u WHERE u.proj_id = ? AND {schedule_task_type_condition("u")}) AS update_activity_count,
            (SELECT COUNT(*) FROM TASK b JOIN TASK u ON u.proj_id = ? AND u.task_code = b.task_code
                WHERE b.proj_id = ? AND {schedule_task_type_condition("b")} AND {schedule_task_type_condition("u")}) AS matched_count,
            (SELECT COUNT(*) FROM TASK u WHERE u.proj_id = ? AND {schedule_task_type_condition("u")}
                AND NOT EXISTS (
                    SELECT 1 FROM TASK b WHERE b.proj_id = ? AND {schedule_task_type_condition("b")} AND b.task_code = u.task_code
                )) AS added_count,
            (SELECT COUNT(*) FROM TASK b WHERE b.proj_id = ? AND {schedule_task_type_condition("b")}
                AND NOT EXISTS (
                    SELECT 1 FROM TASK u WHERE u.proj_id = ? AND {schedule_task_type_condition("u")} AND u.task_code = b.task_code
                )) AS removed_count
    """, (baseline_id, update_id, update_id, baseline_id, update_id, baseline_id, baseline_id, update_id)) or {}


def _paired_activity_cte() -> str:
    return f"""
        WITH baseline_wbs_paths AS (
            SELECT
                w.wbs_id,
                w.parent_wbs_id,
                CAST(w.wbs_name AS varchar(max)) AS wbs_path
            FROM PROJWBS w
            WHERE w.proj_id = ?
              AND (
                  w.parent_wbs_id IS NULL
                  OR w.parent_wbs_id = 0
                  OR NOT EXISTS (
                      SELECT 1 FROM PROJWBS parent
                      WHERE parent.proj_id = w.proj_id
                        AND parent.wbs_id = w.parent_wbs_id
                  )
              )

            UNION ALL

            SELECT
                child.wbs_id,
                child.parent_wbs_id,
                CAST(parent.wbs_path + ' > ' + child.wbs_name AS varchar(max)) AS wbs_path
            FROM PROJWBS child
            JOIN baseline_wbs_paths parent ON parent.wbs_id = child.parent_wbs_id
            WHERE child.proj_id = ?
        ),
        update_wbs_paths AS (
            SELECT
                w.wbs_id,
                w.parent_wbs_id,
                CAST(w.wbs_name AS varchar(max)) AS wbs_path
            FROM PROJWBS w
            WHERE w.proj_id = ?
              AND (
                  w.parent_wbs_id IS NULL
                  OR w.parent_wbs_id = 0
                  OR NOT EXISTS (
                      SELECT 1 FROM PROJWBS parent
                      WHERE parent.proj_id = w.proj_id
                        AND parent.wbs_id = w.parent_wbs_id
                  )
              )

            UNION ALL

            SELECT
                child.wbs_id,
                child.parent_wbs_id,
                CAST(parent.wbs_path + ' > ' + child.wbs_name AS varchar(max)) AS wbs_path
            FROM PROJWBS child
            JOIN update_wbs_paths parent ON parent.wbs_id = child.parent_wbs_id
            WHERE child.proj_id = ?
        ),
        baseline_tasks AS (
            SELECT
                b.task_id,
                b.task_code,
                b.task_name,
                b.status_code,
                b.wbs_id,
                bw.wbs_short_name,
                bw.wbs_name,
                bwp.wbs_path,
                b.target_start_date,
                b.target_end_date,
                b.target_drtn_hr_cnt,
                b.total_float_hr_cnt
            FROM TASK b
            LEFT JOIN PROJWBS bw ON bw.wbs_id = b.wbs_id
            LEFT JOIN baseline_wbs_paths bwp ON bwp.wbs_id = b.wbs_id
            WHERE b.proj_id = ? AND {schedule_task_type_condition("b")}
        ),
        update_tasks AS (
            SELECT
                u.task_id,
                u.task_code,
                u.task_name,
                u.status_code,
                u.wbs_id,
                uw.wbs_short_name,
                uw.wbs_name,
                uwp.wbs_path,
                u.target_start_date,
                u.target_end_date,
                u.target_drtn_hr_cnt,
                u.total_float_hr_cnt
            FROM TASK u
            LEFT JOIN PROJWBS uw ON uw.wbs_id = u.wbs_id
            LEFT JOIN update_wbs_paths uwp ON uwp.wbs_id = u.wbs_id
            WHERE u.proj_id = ? AND {schedule_task_type_condition("u")}
        ),
        paired AS (
            SELECT
                COALESCE(u.task_code, b.task_code) AS task_code,
                COALESCE(u.task_name, b.task_name) AS task_name,
                b.task_id AS baseline_task_id,
                u.task_id AS update_task_id,
                b.task_code AS baseline_task_code,
                u.task_code AS update_task_code,
                b.status_code AS baseline_status,
                u.status_code AS update_status,
                b.wbs_id AS baseline_wbs_id,
                u.wbs_id AS update_wbs_id,
                b.wbs_short_name AS baseline_wbs_short_name,
                u.wbs_short_name AS update_wbs_short_name,
                b.wbs_name AS baseline_wbs_name,
                u.wbs_name AS update_wbs_name,
                b.wbs_path AS baseline_wbs_path,
                u.wbs_path AS update_wbs_path,
                b.target_start_date AS baseline_start,
                u.target_start_date AS update_start,
                b.target_end_date AS baseline_finish,
                u.target_end_date AS update_finish,
                b.target_drtn_hr_cnt AS baseline_duration_hours,
                u.target_drtn_hr_cnt AS update_duration_hours,
                b.total_float_hr_cnt AS baseline_float_hours,
                u.total_float_hr_cnt AS update_float_hours,
                CASE
                    WHEN b.task_code IS NULL THEN 'added'
                    WHEN u.task_code IS NULL THEN 'removed'
                    WHEN ISNULL(CONVERT(varchar, b.target_start_date, 120), '') <> ISNULL(CONVERT(varchar, u.target_start_date, 120), '')
                      OR ISNULL(CONVERT(varchar, b.target_end_date, 120), '') <> ISNULL(CONVERT(varchar, u.target_end_date, 120), '')
                      OR ISNULL(b.target_drtn_hr_cnt, 0) <> ISNULL(u.target_drtn_hr_cnt, 0)
                      OR ISNULL(b.total_float_hr_cnt, 0) <> ISNULL(u.total_float_hr_cnt, 0)
                      OR ISNULL(b.status_code, '') <> ISNULL(u.status_code, '')
                    THEN 'changed'
                    ELSE 'unchanged'
                END AS change_type,
                CASE WHEN b.task_code IS NOT NULL AND u.task_code IS NOT NULL
                    THEN DATEDIFF(day, b.target_start_date, u.target_start_date) END AS start_delta_days,
                CASE WHEN b.task_code IS NOT NULL AND u.task_code IS NOT NULL
                    THEN DATEDIFF(day, b.target_end_date, u.target_end_date) END AS finish_delta_days,
                ISNULL(u.target_drtn_hr_cnt, 0) - ISNULL(b.target_drtn_hr_cnt, 0) AS duration_delta_hours,
                ISNULL(u.total_float_hr_cnt, 0) - ISNULL(b.total_float_hr_cnt, 0) AS float_delta_hours,
                CASE WHEN ISNULL(b.total_float_hr_cnt, 0) < 0 THEN 1 ELSE 0 END AS baseline_negative_float,
                CASE WHEN ISNULL(u.total_float_hr_cnt, 0) < 0 THEN 1 ELSE 0 END AS update_negative_float
            FROM baseline_tasks b
            FULL OUTER JOIN update_tasks u ON u.task_code = b.task_code
        )
    """


def _metric_select(hours_per_day: float) -> str:
    return f"""
            COUNT(*) AS row_count,
            COUNT(DISTINCT task_code) AS activity_count,
            SUM(CASE WHEN baseline_task_code IS NULL AND update_task_code IS NOT NULL THEN 1 ELSE 0 END) AS added_count,
            SUM(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NULL THEN 1 ELSE 0 END) AS removed_count,
            SUM(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NOT NULL THEN 1 ELSE 0 END) AS matched_count,
            SUM(CASE WHEN change_type = 'changed' THEN 1 ELSE 0 END) AS changed_count,
            SUM(ISNULL(start_delta_days, 0)) AS start_slip_days_total,
            SUM(ABS(ISNULL(start_delta_days, 0))) AS start_slip_days_abs_total,
            MAX(start_delta_days) AS start_slip_days_max,
            MAX(ABS(ISNULL(start_delta_days, 0))) AS start_slip_days_max_abs,
            SUM(ISNULL(finish_delta_days, 0)) AS finish_slip_days_total,
            SUM(ABS(ISNULL(finish_delta_days, 0))) AS finish_slip_days_abs_total,
            MAX(finish_delta_days) AS finish_slip_days_max,
            MAX(ABS(ISNULL(finish_delta_days, 0))) AS finish_slip_days_max_abs,
            ROUND(SUM(duration_delta_hours), 2) AS duration_delta_hours,
            ROUND(SUM(duration_delta_hours) / {hours_per_day}, 2) AS duration_delta_days,
            ROUND(SUM(float_delta_hours), 2) AS float_delta_hours,
            ROUND(SUM(float_delta_hours) / {hours_per_day}, 2) AS float_delta_days,
            SUM(baseline_negative_float) AS baseline_negative_float_count,
            SUM(update_negative_float) AS update_negative_float_count,
            SUM(update_negative_float) - SUM(baseline_negative_float) AS negative_float_count_delta,
            ROUND(SUM(CASE WHEN update_negative_float = 1 THEN ISNULL(update_float_hours, 0) ELSE 0 END)
                - SUM(CASE WHEN baseline_negative_float = 1 THEN ISNULL(baseline_float_hours, 0) ELSE 0 END), 2)
                AS negative_float_delta_hours
    """


def _score_expr(cost_expr: str = "0") -> str:
    return f"""
            ROUND(
                SUM(ABS(ISNULL(finish_delta_days, 0))) * 3.0
                + MAX(ABS(ISNULL(finish_delta_days, 0))) * 5.0
                + SUM(ABS(ISNULL(start_delta_days, 0))) * 1.5
                + ABS(SUM(float_delta_hours)) / 8.0
                + ABS(SUM(duration_delta_hours)) / 8.0
                + ABS(SUM(update_negative_float) - SUM(baseline_negative_float)) * 10.0
                + SUM(CASE WHEN baseline_task_code IS NULL OR update_task_code IS NULL THEN 1 ELSE 0 END) * 4.0
                + SUM(CASE WHEN change_type = 'changed' THEN 1 ELSE 0 END) * 2.0
                + {cost_expr},
                2
            ) AS contribution_score
    """


def _activity_projection(hours_per_day: float, alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"""
            {prefix}task_code,
            {prefix}task_name,
            {prefix}change_type,
            {prefix}baseline_status,
            {prefix}update_status,
            CONVERT(varchar, {prefix}baseline_start, 23) AS baseline_start,
            CONVERT(varchar, {prefix}update_start, 23) AS update_start,
            {prefix}start_delta_days,
            CONVERT(varchar, {prefix}baseline_finish, 23) AS baseline_finish,
            CONVERT(varchar, {prefix}update_finish, 23) AS update_finish,
            {prefix}finish_delta_days,
            {prefix}baseline_duration_hours,
            {prefix}update_duration_hours,
            ROUND({prefix}duration_delta_hours, 2) AS duration_delta_hours,
            ROUND({prefix}duration_delta_hours / {hours_per_day}, 2) AS duration_delta_days,
            {prefix}baseline_float_hours,
            {prefix}update_float_hours,
            ROUND({prefix}float_delta_hours, 2) AS float_delta_hours,
            ROUND({prefix}float_delta_hours / {hours_per_day}, 2) AS float_delta_days
    """


def _group_filter(groups: list[dict], key_field: str) -> tuple[str, tuple]:
    keys = [group.get(key_field) for group in groups if group.get(key_field) is not None]
    if not keys:
        return "1 = 0", ()
    placeholders = ", ".join("?" for _ in keys)
    return f"{key_field} IN ({placeholders})", tuple(keys)


def _fetch_wbs_groups(
    baseline_id: int,
    update_id: int,
    hours_per_day: float,
    top_n: int,
) -> tuple[list[dict], list[dict]]:
    params = (baseline_id, baseline_id, update_id, update_id, baseline_id, update_id)
    groups = query(f"""
        {_paired_activity_cte()},
        grouped AS (
            SELECT
                COALESCE(update_wbs_id, baseline_wbs_id, -1) AS group_id,
                COALESCE(update_wbs_short_name, baseline_wbs_short_name, '(no WBS code)') AS group_key,
                COALESCE(update_wbs_name, baseline_wbs_name, '(no WBS)') AS group_name,
                COALESCE(update_wbs_path, baseline_wbs_path, update_wbs_name, baseline_wbs_name, '(no WBS path)') AS group_path,
                {_metric_select(hours_per_day)},
                {_score_expr()}
            FROM paired
            GROUP BY
                COALESCE(update_wbs_id, baseline_wbs_id, -1),
                COALESCE(update_wbs_short_name, baseline_wbs_short_name, '(no WBS code)'),
                COALESCE(update_wbs_name, baseline_wbs_name, '(no WBS)'),
                COALESCE(update_wbs_path, baseline_wbs_path, update_wbs_name, baseline_wbs_name, '(no WBS path)')
        )
        SELECT TOP {top_n} *
        FROM grouped
        ORDER BY contribution_score DESC, finish_slip_days_abs_total DESC, group_key
        OPTION (MAXRECURSION 100)
    """, params)

    if not groups:
        return groups, []
    group_filter, group_params = _group_filter(groups, "group_id")
    details = query(f"""
        {_paired_activity_cte()},
        attributed AS (
            SELECT
                COALESCE(update_wbs_id, baseline_wbs_id, -1) AS group_id,
                COALESCE(update_wbs_short_name, baseline_wbs_short_name, '(no WBS code)') AS group_key,
                COALESCE(update_wbs_name, baseline_wbs_name, '(no WBS)') AS group_name,
                COALESCE(update_wbs_path, baseline_wbs_path, update_wbs_name, baseline_wbs_name, '(no WBS path)') AS group_path,
                {_activity_projection(hours_per_day)},
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(update_wbs_id, baseline_wbs_id, -1)
                    ORDER BY
                        ABS(ISNULL(finish_delta_days, 0)) DESC,
                        ABS(ISNULL(float_delta_hours, 0)) DESC,
                        ABS(ISNULL(duration_delta_hours, 0)) DESC,
                        task_code
                ) AS rn
            FROM paired
            WHERE change_type <> 'unchanged'
        )
        SELECT *
        FROM attributed
        WHERE rn <= 5
          AND {group_filter}
        ORDER BY group_key, rn
        OPTION (MAXRECURSION 100)
    """, (*params, *group_params))
    return groups, details


def _activity_code_cte(
    code_type: str | None,
    code_scope: str | None,
    warnings: list[str],
    params: list,
) -> str:
    type_filter_b = ""
    type_filter_u = ""
    if code_type:
        params.append(_like(code_type))
        type_filter_b = " AND bat.actv_code_type LIKE ? ESCAPE '\\'"
        params.append(_like(code_type))
        type_filter_u = " AND uat.actv_code_type LIKE ? ESCAPE '\\'"

    scope_filter_b, resolved_scope = _code_scope_filter("bat", code_scope, warnings)
    scope_filter_u = ""
    if resolved_scope:
        scope_filter_u = f" AND uat.actv_code_type_scope = '{resolved_scope}'"

    return f"""
        baseline_codes AS (
            SELECT
                p.task_code,
                ta.actv_code_type_id,
                ta.actv_code_id,
                bat.actv_code_type AS code_type,
                bat.actv_code_type_scope AS code_scope,
                bac.short_name AS code_value,
                bac.actv_code_name AS code_value_name,
                CONCAT(bat.actv_code_type_scope, '|', bat.actv_code_type, '|', bac.short_name) AS group_key,
                CONCAT(bat.actv_code_type, ': ', bac.short_name) AS group_name
            FROM paired p
            JOIN TASKACTV ta ON ta.task_id = p.baseline_task_id
            JOIN ACTVCODE bac ON bac.actv_code_id = ta.actv_code_id
                AND bac.actv_code_type_id = ta.actv_code_type_id
            JOIN ACTVTYPE bat ON bat.actv_code_type_id = ta.actv_code_type_id
            WHERE p.baseline_task_id IS NOT NULL
              {type_filter_b}
              {scope_filter_b}
        ),
        update_codes AS (
            SELECT
                p.task_code,
                ta.actv_code_type_id,
                ta.actv_code_id,
                uat.actv_code_type AS code_type,
                uat.actv_code_type_scope AS code_scope,
                uac.short_name AS code_value,
                uac.actv_code_name AS code_value_name,
                CONCAT(uat.actv_code_type_scope, '|', uat.actv_code_type, '|', uac.short_name) AS group_key,
                CONCAT(uat.actv_code_type, ': ', uac.short_name) AS group_name
            FROM paired p
            JOIN TASKACTV ta ON ta.task_id = p.update_task_id
            JOIN ACTVCODE uac ON uac.actv_code_id = ta.actv_code_id
                AND uac.actv_code_type_id = ta.actv_code_type_id
            JOIN ACTVTYPE uat ON uat.actv_code_type_id = ta.actv_code_type_id
            WHERE p.update_task_id IS NOT NULL
              {type_filter_u}
              {scope_filter_u}
        ),
        code_pairs AS (
            SELECT
                COALESCE(u.task_code, b.task_code) AS task_code,
                COALESCE(u.actv_code_type_id, b.actv_code_type_id) AS code_type_id,
                COALESCE(u.actv_code_id, b.actv_code_id) AS code_value_id,
                COALESCE(u.group_key, b.group_key) AS group_key,
                COALESCE(u.group_name, b.group_name) AS group_name,
                COALESCE(u.code_type, b.code_type) AS code_type,
                COALESCE(u.code_scope, b.code_scope) AS code_scope,
                COALESCE(u.code_value, b.code_value) AS code_value,
                COALESCE(u.code_value_name, b.code_value_name) AS code_value_name,
                CASE WHEN b.task_code IS NULL THEN 1 ELSE 0 END AS code_assignment_added,
                CASE WHEN u.task_code IS NULL THEN 1 ELSE 0 END AS code_assignment_removed
            FROM baseline_codes b
            FULL OUTER JOIN update_codes u
              ON u.task_code = b.task_code
             AND u.group_key = b.group_key
        )
    """


def _fetch_activity_code_groups(
    baseline_id: int,
    update_id: int,
    hours_per_day: float,
    top_n: int,
    code_type: str | None,
    code_scope: str | None,
    warnings: list[str],
) -> tuple[list[dict], list[dict], str | None]:
    params: list = [baseline_id, baseline_id, update_id, update_id, baseline_id, update_id]
    code_cte = _activity_code_cte(code_type, code_scope, warnings, params)
    resolved_scope = VALID_CODE_SCOPES.get(str(code_scope).strip().lower().replace("-", "_")) if code_scope else None

    groups = query(f"""
        {_paired_activity_cte()},
        {code_cte},
        attributed AS (
            SELECT
                p.*,
                cp.group_key,
                cp.group_name,
                cp.code_type,
                cp.code_scope,
                cp.code_value,
                cp.code_value_name,
                cp.code_type_id,
                cp.code_value_id,
                cp.code_assignment_added,
                cp.code_assignment_removed
            FROM code_pairs cp
            JOIN paired p ON p.task_code = cp.task_code
        ),
        grouped AS (
            SELECT
                group_key,
                group_name,
                code_type,
                code_scope,
                code_value,
                code_value_name,
                COUNT(DISTINCT code_type_id) AS code_type_count,
                COUNT(DISTINCT code_value_id) AS code_value_count,
                SUM(code_assignment_added) AS code_assignment_added_count,
                SUM(code_assignment_removed) AS code_assignment_removed_count,
                {_metric_select(hours_per_day)},
                {_score_expr("SUM(code_assignment_added + code_assignment_removed) * 3.0")}
            FROM attributed
            GROUP BY group_key, group_name, code_type, code_scope, code_value, code_value_name
        )
        SELECT TOP {top_n} *
        FROM grouped
        ORDER BY contribution_score DESC, finish_slip_days_abs_total DESC, group_key
        OPTION (MAXRECURSION 100)
    """, tuple(params))

    if not groups:
        return groups, [], resolved_scope
    group_filter, group_params = _group_filter(groups, "group_key")
    detail_params: list = [baseline_id, baseline_id, update_id, update_id, baseline_id, update_id]
    detail_code_cte = _activity_code_cte(code_type, code_scope, warnings, detail_params)
    details = query(f"""
        {_paired_activity_cte()},
        {detail_code_cte},
        attributed AS (
            SELECT
                cp.group_key,
                cp.group_name,
                cp.code_type,
                cp.code_scope,
                cp.code_value,
                cp.code_value_name,
                cp.code_assignment_added,
                cp.code_assignment_removed,
                {_activity_projection(hours_per_day, "p")},
                ROW_NUMBER() OVER (
                    PARTITION BY cp.group_key
                    ORDER BY
                        ABS(ISNULL(p.finish_delta_days, 0)) DESC,
                        ABS(ISNULL(p.float_delta_hours, 0)) DESC,
                        ABS(ISNULL(p.duration_delta_hours, 0)) DESC,
                        p.task_code
                ) AS rn
            FROM code_pairs cp
            JOIN paired p ON p.task_code = cp.task_code
            WHERE p.change_type <> 'unchanged'
               OR cp.code_assignment_added = 1
               OR cp.code_assignment_removed = 1
        )
        SELECT *
        FROM attributed
        WHERE rn <= 5
          AND {group_filter}
        ORDER BY group_key, rn
        OPTION (MAXRECURSION 100)
    """, tuple([*detail_params, *group_params]))
    return groups, details, resolved_scope


def _assignment_cte(group_by: str) -> str:
    if group_by == "role":
        key_expr = "CONCAT('role|', COALESCE(CAST(ro.role_id AS varchar(30)), tr.rsrc_type, 'unassigned'))"
        short_expr = "COALESCE(ro.role_short_name, tr.rsrc_type, '(no role)')"
        name_expr = "COALESCE(ro.role_name, tr.rsrc_type, '(no role)')"
    else:
        key_expr = """
            CASE
                WHEN r.rsrc_id IS NOT NULL THEN CONCAT('resource|', CAST(r.rsrc_id AS varchar(30)))
                WHEN ro.role_id IS NOT NULL THEN CONCAT('role|', CAST(ro.role_id AS varchar(30)))
                ELSE CONCAT('unassigned|', COALESCE(tr.rsrc_type, 'unknown'))
            END
        """
        short_expr = "COALESCE(r.rsrc_short_name, ro.role_short_name, tr.rsrc_type, '(unassigned)')"
        name_expr = "COALESCE(r.rsrc_name, ro.role_name, tr.rsrc_type, '(unassigned)')"

    return f"""
        baseline_assignments AS (
            SELECT
                p.task_code,
                {key_expr} AS assignment_key,
                MAX({short_expr}) AS assignment_short_name,
                MAX({name_expr}) AS assignment_name,
                SUM(ISNULL(tr.target_qty, 0)) AS target_qty,
                SUM(ISNULL(tr.act_reg_qty, 0) + ISNULL(tr.act_ot_qty, 0)) AS actual_qty,
                SUM(ISNULL(tr.remain_qty, 0)) AS remaining_qty,
                SUM(ISNULL(tr.target_cost, 0)) AS target_cost,
                SUM(ISNULL(tr.act_reg_cost, 0) + ISNULL(tr.act_ot_cost, 0)) AS actual_cost,
                SUM(ISNULL(tr.remain_cost, 0)) AS remaining_cost
            FROM paired p
            JOIN TASKRSRC tr ON tr.task_id = p.baseline_task_id
            LEFT JOIN RSRC r ON r.rsrc_id = tr.rsrc_id
            LEFT JOIN ROLES ro ON ro.role_id = tr.role_id
            WHERE p.baseline_task_id IS NOT NULL
            GROUP BY p.task_code, {key_expr}
        ),
        update_assignments AS (
            SELECT
                p.task_code,
                {key_expr} AS assignment_key,
                MAX({short_expr}) AS assignment_short_name,
                MAX({name_expr}) AS assignment_name,
                SUM(ISNULL(tr.target_qty, 0)) AS target_qty,
                SUM(ISNULL(tr.act_reg_qty, 0) + ISNULL(tr.act_ot_qty, 0)) AS actual_qty,
                SUM(ISNULL(tr.remain_qty, 0)) AS remaining_qty,
                SUM(ISNULL(tr.target_cost, 0)) AS target_cost,
                SUM(ISNULL(tr.act_reg_cost, 0) + ISNULL(tr.act_ot_cost, 0)) AS actual_cost,
                SUM(ISNULL(tr.remain_cost, 0)) AS remaining_cost
            FROM paired p
            JOIN TASKRSRC tr ON tr.task_id = p.update_task_id
            LEFT JOIN RSRC r ON r.rsrc_id = tr.rsrc_id
            LEFT JOIN ROLES ro ON ro.role_id = tr.role_id
            WHERE p.update_task_id IS NOT NULL
            GROUP BY p.task_code, {key_expr}
        ),
        assignment_pairs AS (
            SELECT
                COALESCE(u.task_code, b.task_code) AS task_code,
                COALESCE(u.assignment_key, b.assignment_key) AS group_key,
                COALESCE(u.assignment_short_name, b.assignment_short_name) AS group_short_name,
                COALESCE(u.assignment_name, b.assignment_name) AS group_name,
                CASE WHEN b.task_code IS NULL THEN 1 ELSE 0 END AS assignment_added,
                CASE WHEN u.task_code IS NULL THEN 1 ELSE 0 END AS assignment_removed,
                CASE WHEN b.task_code IS NOT NULL AND u.task_code IS NOT NULL THEN 1 ELSE 0 END AS assignment_matched,
                ISNULL(u.target_qty, 0) - ISNULL(b.target_qty, 0) AS target_qty_delta,
                ISNULL(u.actual_qty, 0) - ISNULL(b.actual_qty, 0) AS actual_qty_delta,
                ISNULL(u.remaining_qty, 0) - ISNULL(b.remaining_qty, 0) AS remaining_qty_delta,
                ISNULL(u.target_cost, 0) - ISNULL(b.target_cost, 0) AS target_cost_delta,
                ISNULL(u.actual_cost, 0) - ISNULL(b.actual_cost, 0) AS actual_cost_delta,
                ISNULL(u.remaining_cost, 0) - ISNULL(b.remaining_cost, 0) AS remaining_cost_delta
            FROM baseline_assignments b
            FULL OUTER JOIN update_assignments u
              ON u.task_code = b.task_code
             AND u.assignment_key = b.assignment_key
        )
    """


def _fetch_assignment_groups(
    baseline_id: int,
    update_id: int,
    hours_per_day: float,
    top_n: int,
    group_by: str,
) -> tuple[list[dict], list[dict]]:
    params = (baseline_id, baseline_id, update_id, update_id, baseline_id, update_id)
    assignment_cte = _assignment_cte(group_by)

    groups = query(f"""
        {_paired_activity_cte()},
        {assignment_cte},
        attributed AS (
            SELECT
                p.*,
                ap.group_key,
                ap.group_short_name,
                ap.group_name,
                ap.assignment_added,
                ap.assignment_removed,
                ap.assignment_matched,
                ap.target_qty_delta,
                ap.actual_qty_delta,
                ap.remaining_qty_delta,
                ap.target_cost_delta,
                ap.actual_cost_delta,
                ap.remaining_cost_delta
            FROM assignment_pairs ap
            JOIN paired p ON p.task_code = ap.task_code
        ),
        grouped AS (
            SELECT
                group_key,
                group_short_name,
                group_name,
                SUM(assignment_added) AS assignment_added_count,
                SUM(assignment_removed) AS assignment_removed_count,
                SUM(assignment_matched) AS assignment_matched_count,
                ROUND(SUM(target_qty_delta), 2) AS target_qty_delta,
                ROUND(SUM(actual_qty_delta), 2) AS actual_qty_delta,
                ROUND(SUM(remaining_qty_delta), 2) AS remaining_qty_delta,
                ROUND(SUM(target_cost_delta), 2) AS target_cost_delta,
                ROUND(SUM(actual_cost_delta), 2) AS actual_cost_delta,
                ROUND(SUM(remaining_cost_delta), 2) AS remaining_cost_delta,
                {_metric_select(hours_per_day)},
                {_score_expr("(ABS(SUM(target_cost_delta)) + ABS(SUM(remaining_cost_delta))) / 10000.0 + SUM(assignment_added + assignment_removed) * 3.0")}
            FROM attributed
            GROUP BY group_key, group_short_name, group_name
        )
        SELECT TOP {top_n} *
        FROM grouped
        ORDER BY contribution_score DESC, finish_slip_days_abs_total DESC, group_short_name
        OPTION (MAXRECURSION 100)
    """, params)

    if not groups:
        return groups, []
    group_filter, group_params = _group_filter(groups, "group_key")
    details = query(f"""
        {_paired_activity_cte()},
        {assignment_cte},
        attributed AS (
            SELECT
                ap.group_key,
                ap.group_short_name,
                ap.group_name,
                ap.assignment_added,
                ap.assignment_removed,
                ROUND(ap.target_qty_delta, 2) AS target_qty_delta,
                ROUND(ap.actual_qty_delta, 2) AS actual_qty_delta,
                ROUND(ap.remaining_qty_delta, 2) AS remaining_qty_delta,
                ROUND(ap.target_cost_delta, 2) AS target_cost_delta,
                ROUND(ap.actual_cost_delta, 2) AS actual_cost_delta,
                ROUND(ap.remaining_cost_delta, 2) AS remaining_cost_delta,
                {_activity_projection(hours_per_day, "p")},
                ROW_NUMBER() OVER (
                    PARTITION BY ap.group_key
                    ORDER BY
                        ABS(ISNULL(p.finish_delta_days, 0)) DESC,
                        ABS(ISNULL(p.float_delta_hours, 0)) DESC,
                        ABS(ISNULL(ap.target_cost_delta, 0) + ISNULL(ap.remaining_cost_delta, 0)) DESC,
                        p.task_code
                ) AS rn
            FROM assignment_pairs ap
            JOIN paired p ON p.task_code = ap.task_code
            WHERE p.change_type <> 'unchanged'
               OR ap.assignment_added = 1
               OR ap.assignment_removed = 1
               OR ISNULL(ap.target_qty_delta, 0) <> 0
               OR ISNULL(ap.target_cost_delta, 0) <> 0
               OR ISNULL(ap.remaining_cost_delta, 0) <> 0
        )
        SELECT *
        FROM attributed
        WHERE rn <= 5
          AND {group_filter}
        ORDER BY group_short_name, rn
        OPTION (MAXRECURSION 100)
    """, (*params, *group_params))
    return groups, details


def _attach_top_activities(groups: list[dict], details: list[dict], key_field: str = "group_key") -> None:
    by_group: dict[str, list[dict]] = {}
    for row in details:
        key = row.get(key_field)
        if key is None:
            continue
        item = dict(row)
        item.pop("rn", None)
        by_group.setdefault(str(key), []).append(item)

    for group in groups:
        key = group.get(key_field)
        group["top_changed_activities"] = by_group.get(str(key), [])


def get_change_attribution(
    baseline_id: int,
    update_id: int,
    group_by: str = "wbs",
    code_type: str | None = None,
    code_scope: str | None = None,
    top_n: int = 50,
) -> dict:
    """Attribute schedule variance between two P6 project versions to schedule dimensions.

    Activities are matched across versions by TASK.task_code. All SQL is read-only.
    """
    warnings: list[str] = []
    requested_group_by = group_by
    normalized_group_by = (group_by or "wbs").strip().lower()
    if normalized_group_by not in VALID_GROUP_BY:
        warnings.append(
            f"Invalid group_by '{group_by}' was replaced with 'wbs'. "
            "Use one of: wbs, activity_code, resource, role."
        )
        normalized_group_by = "wbs"

    top_n = _limit(top_n)
    baseline = _project(baseline_id)
    update = _project(update_id)
    if not baseline or not update:
        return {
            "error": "Baseline or update project was not found.",
            "filters": {
                "baseline_id": baseline_id,
                "update_id": update_id,
                "group_by": requested_group_by,
                "resolved_group_by": normalized_group_by,
                "code_type": code_type,
                "code_scope": code_scope,
                "top_n": top_n,
            },
            "baseline_project": baseline,
            "update_project": update,
            "warnings": warnings,
        }

    update_settings = get_project_settings(update_id)
    hours_per_day = project_hours_per_day(update_settings)

    resolved_code_scope = None
    if normalized_group_by == "wbs":
        groups, details = _fetch_wbs_groups(baseline_id, update_id, hours_per_day, top_n)
        if code_type or code_scope:
            warnings.append("code_type and code_scope filters apply only when group_by='activity_code'.")
    elif normalized_group_by == "activity_code":
        groups, details, resolved_code_scope = _fetch_activity_code_groups(
            baseline_id,
            update_id,
            hours_per_day,
            top_n,
            code_type,
            code_scope,
            warnings,
        )
        warnings.append(
            "Activity-code attribution includes activities with matching code assignments; uncoded activities are excluded."
        )
    else:
        groups, details = _fetch_assignment_groups(
            baseline_id,
            update_id,
            hours_per_day,
            top_n,
            normalized_group_by,
        )
        if code_type or code_scope:
            warnings.append("code_type and code_scope filters apply only when group_by='activity_code'.")

    attach_key = "group_id" if normalized_group_by == "wbs" else "group_key"
    _attach_top_activities(groups, details, attach_key)
    totals = _totals(baseline_id, update_id)

    return {
        "filters": {
            "baseline_id": baseline_id,
            "update_id": update_id,
            "group_by": requested_group_by,
            "resolved_group_by": normalized_group_by,
            "code_type": code_type,
            "code_scope": resolved_code_scope if resolved_code_scope else code_scope,
            "top_n": top_n,
        },
        "baseline_project": baseline,
        "update_project": update,
        "metadata": {
            "match_key": "TASK.task_code",
            "hours_per_day": hours_per_day,
            "top_n_max": 200,
            "schedule_task_type_condition": schedule_task_type_condition("t"),
            "score_basis": [
                "absolute finish slip",
                "maximum finish slip",
                "absolute start slip",
                "float and duration movement",
                "negative float migration",
                "added/removed/changed activities",
                "assignment and cost deltas for resource/role grouping",
            ],
        },
        "row_counts": {
            **totals,
            "groups_returned": len(groups),
            "detail_rows_returned": len(details),
        },
        "warnings": warnings,
        "groups": groups,
        "notes": [
            "WBS attribution uses update WBS for matched/added activities and baseline WBS for removed activities.",
            "Recursive WBS paths are preferred; WBS names are used when a path is unavailable.",
            "Duration and float day conversions use tools.common.project_hours_per_day for the update project.",
            "Contribution score is a ranking heuristic, not a CPM causation proof.",
        ],
    }

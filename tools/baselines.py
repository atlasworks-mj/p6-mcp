import re
from difflib import SequenceMatcher

from db import query, query_single
from tools.analysis import compare_schedules
from tools.changes import compare_schedule_updates
from tools.common import (
    critical_condition,
    get_project_settings,
    project_hours_per_day,
    schedule_task_type_condition,
)


def _limit(value: int, default: int = 50, maximum: int = 200) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _tokens(text: str | None) -> list[str]:
    return [
        token
        for token in re.split(r"[^a-z0-9]+", (text or "").lower())
        if token and token not in {"schedule", "project", "update", "baseline", "latest"}
    ]


def _norm(text: str | None) -> str:
    return " ".join(_tokens(text))


def _date_key(row: dict) -> str:
    return (
        row.get("last_recalc_date")
        or row.get("last_schedule_date")
        or row.get("update_date")
        or row.get("scd_end_date")
        or row.get("plan_start_date")
        or ""
    )


def _date_source(row: dict) -> str | None:
    for key in ("last_recalc_date", "last_schedule_date", "update_date", "scd_end_date", "plan_start_date"):
        if row.get(key):
            return key
    return None


def _project_task_counts(project_id: int) -> dict:
    row = query_single(f"""
        SELECT
            COUNT(*) AS schedule_activity_count,
            SUM(CASE WHEN task_type = 'TT_Task' THEN 1 ELSE 0 END) AS task_count,
            SUM(CASE WHEN task_type = 'TT_Rsrc' THEN 1 ELSE 0 END) AS resource_dependent_count,
            SUM(CASE WHEN task_type IN ('TT_Mile', 'TT_FinMile') THEN 1 ELSE 0 END) AS milestone_count
        FROM TASK
        WHERE proj_id = ?
          AND {schedule_task_type_condition("")}
    """, (project_id,))
    return row or {
        "schedule_activity_count": 0,
        "task_count": 0,
        "resource_dependent_count": 0,
        "milestone_count": 0,
    }


def _project(project_id: int) -> dict | None:
    return query_single("""
        SELECT
            p.proj_id,
            p.proj_short_name,
            p.description,
            p.project_flag,
            p.orig_proj_id,
            p.sum_base_proj_id,
            p.base_type_id,
            bt.base_type,
            p.use_project_baseline_flag,
            CONVERT(varchar, p.last_baseline_update_date, 23) AS last_baseline_update_date,
            CONVERT(varchar, p.plan_start_date, 23) AS plan_start_date,
            CONVERT(varchar, p.scd_end_date, 23) AS scd_end_date,
            CONVERT(varchar, p.last_recalc_date, 23) AS last_recalc_date,
            CONVERT(varchar, p.last_schedule_date, 23) AS last_schedule_date,
            CONVERT(varchar, p.update_date, 23) AS update_date
        FROM PROJECT p
        LEFT JOIN BASETYPE bt ON bt.base_type_id = p.base_type_id
        WHERE p.proj_id = ?
    """, (project_id,))


def _project_nodes() -> list[dict]:
    return query("""
        SELECT
            w.wbs_id,
            w.parent_wbs_id,
            w.proj_id,
            w.wbs_short_name,
            w.wbs_name,
            w.proj_node_flag,
            p.project_flag,
            p.proj_short_name,
            p.description,
            p.orig_proj_id,
            p.sum_base_proj_id,
            p.base_type_id,
            bt.base_type,
            p.use_project_baseline_flag,
            CONVERT(varchar, p.last_baseline_update_date, 23) AS last_baseline_update_date,
            CONVERT(varchar, p.plan_start_date, 23) AS plan_start_date,
            CONVERT(varchar, p.scd_end_date, 23) AS scd_end_date,
            CONVERT(varchar, p.last_recalc_date, 23) AS last_recalc_date,
            CONVERT(varchar, p.last_schedule_date, 23) AS last_schedule_date,
            CONVERT(varchar, p.update_date, 23) AS update_date
        FROM PROJWBS w
        LEFT JOIN PROJECT p ON p.proj_id = w.proj_id
        LEFT JOIN BASETYPE bt ON bt.base_type_id = p.base_type_id
        WHERE w.proj_node_flag = 'Y'
        ORDER BY w.wbs_short_name
    """)


def _paths(nodes: list[dict]) -> dict[int, str]:
    by_id = {row["wbs_id"]: row for row in nodes}
    cache: dict[int, str] = {}

    def path_for(wbs_id: int | None) -> str:
        if wbs_id is None:
            return ""
        if wbs_id in cache:
            return cache[wbs_id]
        row = by_id.get(wbs_id)
        if not row:
            return ""
        name = row.get("wbs_name") or row.get("wbs_short_name") or str(wbs_id)
        parent = path_for(row.get("parent_wbs_id"))
        path = f"{parent} / {name}" if parent else name
        cache[wbs_id] = path
        return path

    for node in nodes:
        path_for(node["wbs_id"])
    return cache


def _projects_with_paths() -> list[dict]:
    nodes = _project_nodes()
    paths = _paths(nodes)
    projects = []
    for row in nodes:
        if row.get("project_flag") != "Y":
            continue
        parent_path = paths.get(row.get("parent_wbs_id")) or None
        project_path = paths.get(row.get("wbs_id")) or None
        projects.append({
            "proj_id": row["proj_id"],
            "proj_short_name": row["proj_short_name"],
            "project_name": row["wbs_name"],
            "description": row["description"],
            "eps_path": parent_path,
            "project_path": project_path,
            "plan_start_date": row["plan_start_date"],
            "scd_end_date": row["scd_end_date"],
            "last_recalc_date": row["last_recalc_date"],
            "last_schedule_date": row["last_schedule_date"],
            "update_date": row["update_date"],
            "orig_proj_id": row["orig_proj_id"],
            "sum_base_proj_id": row["sum_base_proj_id"],
            "base_type_id": row["base_type_id"],
            "base_type": row.get("base_type"),
            "use_project_baseline_flag": row.get("use_project_baseline_flag"),
            "last_baseline_update_date": row.get("last_baseline_update_date"),
            "project_flag": row.get("project_flag"),
        })
    return projects


def _project_label(row: dict | None) -> str:
    if not row:
        return ""
    return " ".join([
        str(row.get("proj_short_name") or ""),
        str(row.get("project_name") or ""),
        str(row.get("description") or ""),
    ])


def _project_card(project_id: int | None, role: str | None = None) -> dict | None:
    if not project_id:
        return None
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
            p.use_project_baseline_flag,
            CONVERT(varchar, p.last_baseline_update_date, 23) AS last_baseline_update_date,
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
    if row:
        row.update(_project_task_counts(project_id))
        row["timeline_date"] = _date_key(row) or None
        row["timeline_date_source"] = _date_source(row)
    if row and role:
        row["baseline_role"] = role
    return row


def _activity_overlap(anchor_id: int, candidate_id: int) -> dict:
    if anchor_id == candidate_id:
        return {
            "shared_activity_count": None,
            "anchor_activity_count": None,
            "candidate_activity_count": None,
            "overlap_ratio": 1.0,
        }
    row = query_single(f"""
        SELECT
            (SELECT COUNT(*) FROM TASK a WHERE a.proj_id = ? AND {schedule_task_type_condition("a")}) AS anchor_activity_count,
            (SELECT COUNT(*) FROM TASK c WHERE c.proj_id = ? AND {schedule_task_type_condition("c")}) AS candidate_activity_count,
            (
                SELECT COUNT(*)
                FROM TASK a
                JOIN TASK c ON c.proj_id = ?
                    AND {schedule_task_type_condition("c")}
                    AND c.task_code = a.task_code
                WHERE a.proj_id = ?
                  AND {schedule_task_type_condition("a")}
            ) AS shared_activity_count
    """, (anchor_id, candidate_id, candidate_id, anchor_id))
    if not row:
        return {
            "shared_activity_count": 0,
            "anchor_activity_count": 0,
            "candidate_activity_count": 0,
            "overlap_ratio": 0.0,
        }
    anchor_count = row.get("anchor_activity_count") or 0
    candidate_count = row.get("candidate_activity_count") or 0
    shared = row.get("shared_activity_count") or 0
    denominator = min(anchor_count, candidate_count) or max(anchor_count, candidate_count) or 1
    row["overlap_ratio"] = round(shared / denominator, 3)
    return row


def _role_guess(row: dict, anchor: dict | None = None) -> str:
    if anchor and row.get("proj_id") == anchor.get("proj_id"):
        return "anchor"
    if anchor and row.get("proj_id") in {anchor.get("orig_proj_id"), anchor.get("sum_base_proj_id")}:
        return "assigned_baseline"
    if anchor and anchor.get("proj_id") in {row.get("orig_proj_id"), row.get("sum_base_proj_id")}:
        return "uses_anchor_as_baseline"
    if row.get("base_type_id") or row.get("orig_proj_id"):
        return "baseline"
    return "update_candidate"


def _resolve_baseline(project_id: int, baseline: str | int | None = "assigned") -> dict:
    project = _project(project_id)
    if not project:
        return {"error": f"Project {project_id} was not found."}

    selected_id = None
    source = None
    requested = baseline if baseline is not None else "assigned"

    if isinstance(requested, int):
        selected_id = requested
        source = "explicit_proj_id"
    else:
        text = str(requested).strip().lower()
        if text in {"assigned", "", "none"}:
            selected_id = project.get("sum_base_proj_id") or project.get("orig_proj_id")
            source = "sum_base_proj_id" if project.get("sum_base_proj_id") else "orig_proj_id"
        elif text == "summary":
            selected_id = project.get("sum_base_proj_id")
            source = "sum_base_proj_id"
        elif text == "original":
            selected_id = project.get("orig_proj_id")
            source = "orig_proj_id"
        else:
            try:
                selected_id = int(text)
                source = "explicit_proj_id"
            except ValueError:
                return {"error": f"Unknown baseline selector '{baseline}'."}

    if not selected_id:
        return {
            "error": f"No baseline project is assigned to project {project_id}.",
            "project": project,
            "requested_baseline": baseline,
            "resolution_source": source,
        }

    baseline_project = _project_card(selected_id)
    if not baseline_project:
        return {
            "error": f"Resolved baseline project {selected_id} was not found.",
            "project": project,
            "requested_baseline": baseline,
            "resolution_source": source,
        }

    return {
        "project": project,
        "baseline": baseline_project,
        "baseline_id": selected_id,
        "requested_baseline": baseline,
        "resolution_source": source,
    }


def get_project_baselines(project_id: int) -> dict:
    """Return baseline assignments and likely baseline relatives for a project."""
    project = _project_card(project_id)
    if not project:
        return {"error": f"Project {project_id} was not found.", "filters": {"project_id": project_id}}

    assigned_ids = [
        ("summary", project.get("sum_base_proj_id")),
        ("original", project.get("orig_proj_id")),
    ]
    assigned = []
    seen = set()
    for role, baseline_id in assigned_ids:
        card = _project_card(baseline_id, role)
        if card and baseline_id not in seen:
            assigned.append(card)
            seen.add(baseline_id)

    referenced_by = query("""
        SELECT
            p.proj_id,
            p.proj_short_name,
            p.description,
            p.orig_proj_id,
            p.sum_base_proj_id,
            p.base_type_id,
            bt.base_type,
            p.use_project_baseline_flag,
            CONVERT(varchar, p.last_baseline_update_date, 23) AS last_baseline_update_date,
            CONVERT(varchar, p.plan_start_date, 23) AS plan_start_date,
            CONVERT(varchar, p.scd_end_date, 23) AS scd_end_date,
            CONVERT(varchar, p.last_recalc_date, 23) AS last_recalc_date,
            CONVERT(varchar, p.last_schedule_date, 23) AS last_schedule_date,
            w.wbs_name AS project_name,
            CASE
                WHEN p.sum_base_proj_id = ? THEN 'uses_as_summary_baseline'
                WHEN p.orig_proj_id = ? THEN 'uses_as_original_baseline'
                ELSE 'related'
            END AS relationship
        FROM PROJECT p
        LEFT JOIN BASETYPE bt ON bt.base_type_id = p.base_type_id
        LEFT JOIN PROJWBS w ON w.proj_id = p.proj_id AND w.proj_node_flag = 'Y'
        WHERE p.sum_base_proj_id = ? OR p.orig_proj_id = ?
        ORDER BY COALESCE(p.last_recalc_date, p.scd_end_date, p.plan_start_date), p.proj_short_name
    """, (project_id, project_id, project_id, project_id))

    same_original = []
    if project.get("orig_proj_id"):
        same_original = query("""
            SELECT
                p.proj_id,
                p.proj_short_name,
                p.description,
                p.orig_proj_id,
                p.sum_base_proj_id,
                p.base_type_id,
                bt.base_type,
                p.use_project_baseline_flag,
                CONVERT(varchar, p.last_baseline_update_date, 23) AS last_baseline_update_date,
                CONVERT(varchar, p.plan_start_date, 23) AS plan_start_date,
                CONVERT(varchar, p.scd_end_date, 23) AS scd_end_date,
                CONVERT(varchar, p.last_recalc_date, 23) AS last_recalc_date,
                CONVERT(varchar, p.last_schedule_date, 23) AS last_schedule_date,
                w.wbs_name AS project_name
            FROM PROJECT p
            LEFT JOIN BASETYPE bt ON bt.base_type_id = p.base_type_id
            LEFT JOIN PROJWBS w ON w.proj_id = p.proj_id AND w.proj_node_flag = 'Y'
            WHERE p.orig_proj_id = ? AND p.proj_id <> ?
            ORDER BY COALESCE(p.last_recalc_date, p.scd_end_date, p.plan_start_date), p.proj_short_name
        """, (project.get("orig_proj_id"), project_id))

    return {
        "filters": {"project_id": project_id},
        "project": project,
        "selected_baseline_ids": {
            "summary": project.get("sum_base_proj_id"),
            "original": project.get("orig_proj_id"),
        },
        "row_counts": {
            "assigned_baselines": len(assigned),
            "projects_referencing_this_project": len(referenced_by),
            "same_original_project": len(same_original),
        },
        "unresolved_baseline_ids": {
            role: baseline_id
            for role, baseline_id in assigned_ids
            if baseline_id and baseline_id not in seen
        },
        "assigned_baselines": assigned,
        "projects_referencing_this_project": referenced_by,
        "same_original_project": same_original,
        "notes": [
            "P6 baseline fields can be null; missing assignments are returned as null IDs.",
            "summary uses PROJECT.sum_base_proj_id; original uses PROJECT.orig_proj_id.",
        ],
    }


def find_related_updates(
    project_id: int | None = None,
    search: str | None = None,
    top_n: int = 50,
) -> dict:
    """Find likely schedule updates related by baseline links, EPS path, name, and dates."""
    top_n = _limit(top_n)
    projects = _projects_with_paths()
    anchor = next((row for row in projects if row.get("proj_id") == project_id), None)
    if project_id is not None and not anchor:
        return {"error": f"Project {project_id} was not found.", "filters": {"project_id": project_id, "search": search}}
    if project_id is None and not search:
        return {"error": "Provide project_id, search, or both.", "filters": {"project_id": project_id, "search": search}}

    search_tokens = _tokens(search)
    anchor_tokens = _tokens(_project_label(anchor)) if anchor else []
    anchor_name = _norm(_project_label(anchor)) if anchor else ""
    anchor_orig = anchor.get("orig_proj_id") if anchor else None
    anchor_summary = anchor.get("sum_base_proj_id") if anchor else None

    scored = []
    for row in projects:
        score = 0
        reasons = []
        haystack = " ".join([
            str(row.get("proj_short_name") or ""),
            str(row.get("project_name") or ""),
            str(row.get("description") or ""),
            str(row.get("eps_path") or ""),
        ]).lower()

        if anchor:
            if row["proj_id"] == anchor["proj_id"]:
                score += 25
                reasons.append("anchor_project")
            if row.get("eps_path") and row.get("eps_path") == anchor.get("eps_path"):
                score += 30
                reasons.append("same_eps_path")
            if row["proj_id"] in {anchor_orig, anchor_summary}:
                score += 70
                reasons.append("assigned_baseline")
            if row.get("orig_proj_id") and row.get("orig_proj_id") == anchor_orig:
                score += 45
                reasons.append("same_orig_proj_id")
            if row.get("sum_base_proj_id") == anchor["proj_id"] or row.get("orig_proj_id") == anchor["proj_id"]:
                score += 55
                reasons.append("references_anchor_as_baseline")
            if anchor["proj_id"] in {row.get("orig_proj_id"), row.get("sum_base_proj_id")}:
                score += 55
                reasons.append("anchor_is_assigned_baseline")

            row_name = _norm(_project_label(row))
            similarity = SequenceMatcher(None, anchor_name, row_name).ratio() if anchor_name and row_name else 0
            if similarity >= 0.75:
                score += 30
                reasons.append(f"name_similarity_{similarity:.2f}")
            elif similarity >= 0.55:
                score += 15
                reasons.append(f"name_similarity_{similarity:.2f}")

            shared_tokens = sorted(set(anchor_tokens) & set(_tokens(_project_label(row))))
            if shared_tokens:
                score += min(20, len(shared_tokens) * 4)
                reasons.append("shared_name_tokens:" + ",".join(shared_tokens[:5]))

        if search_tokens:
            matched = [token for token in search_tokens if token in haystack]
            if matched:
                score += 20 + len(matched) * 12
                reasons.append("search_tokens:" + ",".join(matched[:5]))

        if score <= 0:
            continue
        item = dict(row)
        item["match_score"] = score
        item["match_reasons"] = reasons
        item["timeline_date"] = _date_key(item) or None
        item["timeline_date_source"] = _date_source(item)
        item["role_guess"] = _role_guess(item, anchor)
        scored.append(item)

    scored.sort(key=lambda r: (r["match_score"], _date_key(r), r.get("proj_short_name") or ""), reverse=True)
    selected = scored[:top_n]
    if anchor:
        for row in selected:
            overlap = _activity_overlap(anchor["proj_id"], row["proj_id"])
            row["activity_overlap"] = overlap
            if overlap.get("overlap_ratio") is not None:
                row["match_confidence"] = (
                    "high" if overlap["overlap_ratio"] >= 0.85 or row["role_guess"] in {"assigned_baseline", "anchor"} else
                    "medium" if overlap["overlap_ratio"] >= 0.5 else
                    "low"
                )
            else:
                row["match_confidence"] = "self"

    groups: dict[str, dict] = {}
    for row in selected:
        key = row.get("eps_path") or "(no EPS path)"
        groups.setdefault(key, {"group": key, "project_count": 0, "projects": []})
        groups[key]["project_count"] += 1
        groups[key]["projects"].append(row)

    return {
        "filters": {"project_id": project_id, "search": search, "top_n": top_n},
        "anchor_project": anchor,
        "row_counts": {"candidates_scored": len(scored), "returned": len(selected), "groups": len(groups)},
        "heuristics": [
            "baseline links via orig_proj_id and sum_base_proj_id get the strongest boost.",
            "same EPS path, shared original baseline, name token overlap, and date-sort recency are secondary signals.",
            "search text is matched against project code, project/WBS name, description, and EPS path.",
        ],
        "projects": selected,
        "groups": sorted(groups.values(), key=lambda g: (-g["project_count"], g["group"])),
        "notes": [
            "This is a heuristic grouping, not a guaranteed P6 update chain.",
            "Use get_update_timeline to review chronological ordering and deltas.",
        ],
    }


def compare_to_baseline(project_id: int, baseline: str | int = "assigned", top_n: int = 50) -> dict:
    """Compare a project to an assigned, named, or explicit baseline project."""
    top_n = _limit(top_n)
    resolved = _resolve_baseline(project_id, baseline)
    if "error" in resolved:
        return {**resolved, "filters": {"project_id": project_id, "baseline": baseline, "top_n": top_n}}

    baseline_id = resolved["baseline_id"]
    summary_compare = compare_schedules(baseline_id, project_id, top_n=top_n)
    detailed_compare = compare_schedule_updates(baseline_id, project_id, top_n=top_n)

    return {
        "filters": {"project_id": project_id, "baseline": baseline, "top_n": top_n},
        "selected_baseline_ids": {
            "baseline_id": baseline_id,
            "resolution_source": resolved["resolution_source"],
            "requested_baseline": baseline,
        },
        "project": resolved["project"],
        "baseline_project": resolved["baseline"],
        "row_counts": {
            "changed_activities": detailed_compare.get("changed_activity_sample_count") if isinstance(detailed_compare, dict) else None,
            "added_activities": detailed_compare.get("added_activity_sample_count") if isinstance(detailed_compare, dict) else None,
            "removed_activities": detailed_compare.get("removed_activity_sample_count") if isinstance(detailed_compare, dict) else None,
        },
        "summary_comparison": summary_compare,
        "detailed_comparison": detailed_compare,
        "notes": [
            "baseline='assigned' prefers PROJECT.sum_base_proj_id, then PROJECT.orig_proj_id.",
            "baseline='summary' uses sum_base_proj_id; baseline='original' uses orig_proj_id; integers are treated as explicit proj_id values.",
        ],
    }


def _timeline_metrics(project_id: int) -> dict:
    project = get_project_settings(project_id)
    critical_sql, critical_params, critical_basis, threshold_hours = critical_condition(project)
    row = query_single(f"""
        SELECT
            COUNT(*) AS activity_count,
            CONVERT(varchar, MIN(t.target_start_date), 23) AS planned_start,
            CONVERT(varchar, MAX(t.target_end_date), 23) AS planned_finish,
            SUM(CASE WHEN t.status_code <> 'TK_Complete' AND {critical_sql} THEN 1 ELSE 0 END) AS critical_count,
            SUM(CASE WHEN t.status_code <> 'TK_Complete' AND t.total_float_hr_cnt < 0 THEN 1 ELSE 0 END) AS negative_float_count,
            SUM(CASE WHEN t.status_code = 'TK_Complete' THEN 1 ELSE 0 END) AS complete_count,
            SUM(CASE WHEN t.status_code = 'TK_Active' THEN 1 ELSE 0 END) AS in_progress_count,
            SUM(CASE WHEN t.status_code = 'TK_NotStart' THEN 1 ELSE 0 END) AS not_started_count
        FROM TASK t
        WHERE t.proj_id = ? AND {schedule_task_type_condition("t")}
    """, (*critical_params, project_id))
    row = row or {}
    row["critical_basis"] = critical_basis
    row["critical_threshold_hours"] = threshold_hours
    row["critical_threshold_days"] = round(threshold_hours / project_hours_per_day(project), 2)
    row["hours_per_day"] = project_hours_per_day(project)
    return row


def get_update_timeline(
    project_id: int | None = None,
    search: str | None = None,
    top_n: int = 50,
) -> dict:
    """Return likely related updates in chronological order with schedule deltas."""
    top_n = _limit(top_n)
    related = find_related_updates(project_id=project_id, search=search, top_n=top_n)
    if "error" in related:
        return related

    projects = related.get("projects", [])
    timeline = []
    for project in projects:
        metrics = _timeline_metrics(project["proj_id"])
        row = {**project, **metrics}
        row["timeline_date"] = _date_key(row) or None
        row["timeline_date_source"] = _date_source(row)
        timeline.append(row)

    timeline.sort(key=lambda r: (_date_key(r), r.get("proj_short_name") or "", r.get("proj_id") or 0))
    previous = None
    for row in timeline:
        if previous:
            finish_delta = query_single("""
                SELECT DATEDIFF(day,
                    (SELECT scd_end_date FROM PROJECT WHERE proj_id = ?),
                    (SELECT scd_end_date FROM PROJECT WHERE proj_id = ?)
                ) AS value
            """, (previous["proj_id"], row["proj_id"]))
            row["delta_from_previous"] = {
                "previous_proj_id": previous.get("proj_id"),
                "finish_delta_days": finish_delta.get("value") if finish_delta else None,
                "activity_count_delta": (row.get("activity_count") or 0) - (previous.get("activity_count") or 0),
                "critical_count_delta": (row.get("critical_count") or 0) - (previous.get("critical_count") or 0),
                "negative_float_delta": (row.get("negative_float_count") or 0) - (previous.get("negative_float_count") or 0),
                "complete_count_delta": (row.get("complete_count") or 0) - (previous.get("complete_count") or 0),
            }
        else:
            row["delta_from_previous"] = None
        previous = row

    return {
        "filters": {"project_id": project_id, "search": search, "top_n": top_n},
        "row_counts": {"related_projects": len(projects), "timeline_rows": len(timeline)},
        "heuristics": related.get("heuristics", []),
        "timeline": timeline,
        "notes": [
            "Chronology uses last_recalc_date, falling back to scheduled finish and planned start.",
            "Critical counts follow each project's P6 critical setting.",
            "Hours-per-day comes from the project calendar through tools.common.project_hours_per_day.",
        ],
    }


def get_baseline_variance_by_wbs(
    update_id: int,
    baseline_id: int | None = None,
    top_n: int = 100,
) -> dict:
    """Compare update and baseline TASK rows by task_code and roll variance up by WBS."""
    top_n = _limit(top_n, default=100, maximum=200)
    if baseline_id is None:
        resolved = _resolve_baseline(update_id, "assigned")
        if "error" in resolved:
            return {**resolved, "filters": {"update_id": update_id, "baseline_id": baseline_id, "top_n": top_n}}
        baseline_id = resolved["baseline_id"]
        baseline_source = resolved["resolution_source"]
    else:
        baseline_source = "explicit_proj_id"

    update = _project_card(update_id)
    baseline = _project_card(baseline_id)
    if not update or not baseline:
        return {
            "error": "Update or baseline project was not found.",
            "filters": {"update_id": update_id, "baseline_id": baseline_id, "top_n": top_n},
        }

    update_settings = get_project_settings(update_id)
    hours_per_day = project_hours_per_day(update_settings)

    wbs_rows = query(f"""
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
                b.task_code,
                b.task_name,
                b.status_code,
                b.target_start_date,
                b.target_end_date,
                b.target_drtn_hr_cnt,
                b.total_float_hr_cnt,
                w.wbs_short_name,
                w.wbs_name,
                bwp.wbs_path
            FROM TASK b
            LEFT JOIN PROJWBS w ON w.wbs_id = b.wbs_id
            LEFT JOIN baseline_wbs_paths bwp ON bwp.wbs_id = b.wbs_id
            WHERE b.proj_id = ? AND {schedule_task_type_condition("b")}
        ),
        update_tasks AS (
            SELECT
                u.task_code,
                u.task_name,
                u.status_code,
                u.target_start_date,
                u.target_end_date,
                u.target_drtn_hr_cnt,
                u.total_float_hr_cnt,
                w.wbs_short_name,
                w.wbs_name,
                uwp.wbs_path
            FROM TASK u
            LEFT JOIN PROJWBS w ON w.wbs_id = u.wbs_id
            LEFT JOIN update_wbs_paths uwp ON uwp.wbs_id = u.wbs_id
            WHERE u.proj_id = ? AND {schedule_task_type_condition("u")}
        ),
        paired AS (
            SELECT
                COALESCE(u.wbs_short_name, b.wbs_short_name, '(no WBS code)') AS wbs_short_name,
                COALESCE(u.wbs_name, b.wbs_name, '(no WBS)') AS wbs_name,
                COALESCE(u.wbs_path, b.wbs_path, '(no WBS path)') AS wbs_path,
                b.task_code AS baseline_task_code,
                u.task_code AS update_task_code,
                b.status_code AS baseline_status,
                u.status_code AS update_status,
                b.target_start_date AS baseline_start,
                u.target_start_date AS update_start,
                b.target_end_date AS baseline_finish,
                u.target_end_date AS update_finish,
                b.target_drtn_hr_cnt AS baseline_duration_hours,
                u.target_drtn_hr_cnt AS update_duration_hours,
                b.total_float_hr_cnt AS baseline_float_hours,
                u.total_float_hr_cnt AS update_float_hours
            FROM baseline_tasks b
            FULL OUTER JOIN update_tasks u ON u.task_code = b.task_code
        )
        SELECT TOP {top_n}
            wbs_short_name,
            wbs_name,
            wbs_path,
            SUM(CASE WHEN baseline_task_code IS NOT NULL THEN 1 ELSE 0 END) AS baseline_activity_count,
            SUM(CASE WHEN update_task_code IS NOT NULL THEN 1 ELSE 0 END) AS update_activity_count,
            SUM(CASE WHEN baseline_task_code IS NULL AND update_task_code IS NOT NULL THEN 1 ELSE 0 END) AS added_count,
            SUM(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NULL THEN 1 ELSE 0 END) AS removed_count,
            SUM(CASE WHEN baseline_status = 'TK_Complete' THEN 1 ELSE 0 END) AS baseline_complete_count,
            SUM(CASE WHEN update_status = 'TK_Complete' THEN 1 ELSE 0 END) AS update_complete_count,
            AVG(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NOT NULL
                THEN DATEDIFF(day, baseline_start, update_start) END) AS avg_start_variance_days,
            MAX(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NOT NULL
                THEN ABS(DATEDIFF(day, baseline_start, update_start)) END) AS max_abs_start_variance_days,
            AVG(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NOT NULL
                THEN DATEDIFF(day, baseline_finish, update_finish) END) AS avg_finish_variance_days,
            MAX(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NOT NULL
                THEN ABS(DATEDIFF(day, baseline_finish, update_finish)) END) AS max_abs_finish_variance_days,
            SUM(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NOT NULL
                THEN ISNULL(update_duration_hours, 0) - ISNULL(baseline_duration_hours, 0) ELSE 0 END) AS duration_variance_hours,
            ROUND(SUM(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NOT NULL
                THEN ISNULL(update_duration_hours, 0) - ISNULL(baseline_duration_hours, 0) ELSE 0 END) / ?, 1) AS duration_variance_days,
            SUM(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NOT NULL
                THEN ISNULL(update_float_hours, 0) - ISNULL(baseline_float_hours, 0) ELSE 0 END) AS float_variance_hours,
            ROUND(SUM(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NOT NULL
                THEN ISNULL(update_float_hours, 0) - ISNULL(baseline_float_hours, 0) ELSE 0 END) / ?, 1) AS float_variance_days
        FROM paired
        GROUP BY wbs_short_name, wbs_name, wbs_path
        ORDER BY
            ABS(SUM(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NOT NULL
                THEN ISNULL(update_float_hours, 0) - ISNULL(baseline_float_hours, 0) ELSE 0 END)) DESC,
            MAX(CASE WHEN baseline_task_code IS NOT NULL AND update_task_code IS NOT NULL
                THEN ABS(DATEDIFF(day, baseline_finish, update_finish)) END) DESC,
            added_count DESC,
            removed_count DESC,
            wbs_short_name
    """, (
        baseline_id,
        baseline_id,
        update_id,
        update_id,
        baseline_id,
        update_id,
        hours_per_day,
        hours_per_day,
    ))

    top_variance_rows = query(f"""
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
            SELECT b.*, bwp.wbs_path
            FROM TASK b
            LEFT JOIN baseline_wbs_paths bwp ON bwp.wbs_id = b.wbs_id
            WHERE b.proj_id = ? AND {schedule_task_type_condition("b")}
        ),
        update_tasks AS (
            SELECT u.*, uwp.wbs_path
            FROM TASK u
            LEFT JOIN update_wbs_paths uwp ON uwp.wbs_id = u.wbs_id
            WHERE u.proj_id = ? AND {schedule_task_type_condition("u")}
        )
        SELECT TOP {top_n}
            COALESCE(uw.wbs_short_name, bw.wbs_short_name, '(no WBS code)') AS wbs_short_name,
            COALESCE(uw.wbs_name, bw.wbs_name, '(no WBS)') AS wbs_name,
            COALESCE(u.wbs_path, b.wbs_path, '(no WBS path)') AS wbs_path,
            COALESCE(u.task_code, b.task_code) AS task_code,
            COALESCE(u.task_name, b.task_name) AS task_name,
            CASE
                WHEN b.task_code IS NULL THEN 'added'
                WHEN u.task_code IS NULL THEN 'removed'
                ELSE 'matched'
            END AS variance_type,
            b.status_code AS baseline_status,
            u.status_code AS update_status,
            CONVERT(varchar, b.target_start_date, 23) AS baseline_start,
            CONVERT(varchar, u.target_start_date, 23) AS update_start,
            DATEDIFF(day, b.target_start_date, u.target_start_date) AS start_variance_days,
            CONVERT(varchar, b.target_end_date, 23) AS baseline_finish,
            CONVERT(varchar, u.target_end_date, 23) AS update_finish,
            DATEDIFF(day, b.target_end_date, u.target_end_date) AS finish_variance_days,
            b.target_drtn_hr_cnt AS baseline_duration_hours,
            u.target_drtn_hr_cnt AS update_duration_hours,
            ISNULL(u.target_drtn_hr_cnt, 0) - ISNULL(b.target_drtn_hr_cnt, 0) AS duration_variance_hours,
            ROUND((ISNULL(u.target_drtn_hr_cnt, 0) - ISNULL(b.target_drtn_hr_cnt, 0)) / ?, 1) AS duration_variance_days,
            b.total_float_hr_cnt AS baseline_float_hours,
            u.total_float_hr_cnt AS update_float_hours,
            ISNULL(u.total_float_hr_cnt, 0) - ISNULL(b.total_float_hr_cnt, 0) AS float_variance_hours,
            ROUND((ISNULL(u.total_float_hr_cnt, 0) - ISNULL(b.total_float_hr_cnt, 0)) / ?, 1) AS float_variance_days
        FROM baseline_tasks b
        FULL OUTER JOIN update_tasks u ON u.task_code = b.task_code
        LEFT JOIN PROJWBS bw ON bw.wbs_id = b.wbs_id
        LEFT JOIN PROJWBS uw ON uw.wbs_id = u.wbs_id
        WHERE (
              b.task_code IS NULL
              OR u.task_code IS NULL
              OR ISNULL(CONVERT(varchar, b.target_start_date, 120), '') <> ISNULL(CONVERT(varchar, u.target_start_date, 120), '')
              OR ISNULL(CONVERT(varchar, b.target_end_date, 120), '') <> ISNULL(CONVERT(varchar, u.target_end_date, 120), '')
              OR ISNULL(b.target_drtn_hr_cnt, 0) <> ISNULL(u.target_drtn_hr_cnt, 0)
              OR ISNULL(b.total_float_hr_cnt, 0) <> ISNULL(u.total_float_hr_cnt, 0)
              OR ISNULL(b.status_code, '') <> ISNULL(u.status_code, '')
          )
        ORDER BY
            ABS(DATEDIFF(day, b.target_end_date, u.target_end_date)) DESC,
            ABS(ISNULL(u.total_float_hr_cnt, 0) - ISNULL(b.total_float_hr_cnt, 0)) DESC,
            ABS(ISNULL(u.target_drtn_hr_cnt, 0) - ISNULL(b.target_drtn_hr_cnt, 0)) DESC,
            COALESCE(u.task_code, b.task_code)
    """, (
        baseline_id,
        baseline_id,
        update_id,
        update_id,
        baseline_id,
        update_id,
        hours_per_day,
        hours_per_day,
    ))

    totals = query_single(f"""
        SELECT
            (SELECT COUNT(*) FROM TASK b WHERE b.proj_id = ? AND {schedule_task_type_condition("b")}) AS baseline_activity_count,
            (SELECT COUNT(*) FROM TASK u WHERE u.proj_id = ? AND {schedule_task_type_condition("u")}) AS update_activity_count,
            (SELECT COUNT(*) FROM TASK u WHERE u.proj_id = ? AND {schedule_task_type_condition("u")}
                AND NOT EXISTS (
                    SELECT 1 FROM TASK b WHERE b.proj_id = ? AND {schedule_task_type_condition("b")} AND b.task_code = u.task_code
                )) AS added_count,
            (SELECT COUNT(*) FROM TASK b WHERE b.proj_id = ? AND {schedule_task_type_condition("b")}
                AND NOT EXISTS (
                    SELECT 1 FROM TASK u WHERE u.proj_id = ? AND {schedule_task_type_condition("u")} AND u.task_code = b.task_code
                )) AS removed_count
    """, (baseline_id, update_id, update_id, baseline_id, baseline_id, update_id))

    return {
        "filters": {"update_id": update_id, "baseline_id": baseline_id, "top_n": top_n},
        "selected_baseline_ids": {"baseline_id": baseline_id, "resolution_source": baseline_source},
        "update_project": update,
        "baseline_project": baseline,
        "row_counts": {
            "wbs_rows_returned": len(wbs_rows),
            "top_variance_rows_returned": len(top_variance_rows),
            **(totals or {}),
        },
        "hours_per_day": hours_per_day,
        "wbs_variance": wbs_rows,
        "top_variance_rows": top_variance_rows,
        "notes": [
            "Activities are matched by TASK.task_code across baseline and update.",
            "Day conversions use the update project's project calendar hours-per-day, not a fixed 8h day.",
            "WBS is grouped by the update WBS when present, otherwise by the baseline WBS.",
        ],
    }

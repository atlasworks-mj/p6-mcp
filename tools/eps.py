import re

from db import query


def _project_nodes() -> list[dict]:
    return query("""
        SELECT
            w.wbs_id,
            w.parent_wbs_id,
            w.proj_id,
            w.wbs_short_name,
            w.wbs_name,
            p.project_flag,
            p.proj_short_name,
            p.description,
            CONVERT(varchar, p.plan_start_date, 23) AS plan_start_date,
            CONVERT(varchar, p.scd_end_date, 23) AS scd_end_date,
            CONVERT(varchar, p.last_recalc_date, 23) AS last_recalc_date
        FROM PROJWBS w
        LEFT JOIN PROJECT p ON p.proj_id = w.proj_id
        WHERE w.proj_node_flag = 'Y'
        ORDER BY w.wbs_short_name
    """)


def _build_paths(nodes: list[dict]) -> dict[int, str]:
    by_id = {row["wbs_id"]: row for row in nodes}
    cache: dict[int, str] = {}

    def path_for(wbs_id: int) -> str:
        if wbs_id in cache:
            return cache[wbs_id]
        row = by_id.get(wbs_id)
        if not row:
            return ""
        name = row.get("wbs_name") or row.get("wbs_short_name") or str(wbs_id)
        parent_id = row.get("parent_wbs_id")
        parent_path = path_for(parent_id) if parent_id in by_id else ""
        path = f"{parent_path} / {name}" if parent_path else name
        cache[wbs_id] = path
        return path

    for node in nodes:
        path_for(node["wbs_id"])
    return cache


def _depth(path: str) -> int:
    return 0 if not path else path.count(" / ")


def get_eps_tree(include_projects: bool = True) -> list[dict]:
    """Return the EPS/project tree as a flattened hierarchy.

    Args:
        include_projects: When true, include actual project nodes as leaves.

    Returns:
        Rows with node_type ("eps" or "project"), EPS path, parent id, and
        project metadata for project nodes.
    """
    nodes = _project_nodes()
    paths = _build_paths(nodes)
    rows = []

    for row in nodes:
        is_project = row.get("project_flag") == "Y"
        if is_project and not include_projects:
            continue
        path = paths.get(row["wbs_id"], "")
        parent_path = paths.get(row["parent_wbs_id"], "")
        rows.append({
            "node_type": "project" if is_project else "eps",
            "depth": _depth(path),
            "wbs_id": row["wbs_id"],
            "parent_wbs_id": row["parent_wbs_id"],
            "eps_path": path,
            "parent_path": parent_path or None,
            "wbs_short_name": row["wbs_short_name"],
            "wbs_name": row["wbs_name"],
            "proj_id": row["proj_id"] if is_project else None,
            "proj_short_name": row["proj_short_name"] if is_project else None,
            "description": row["description"] if is_project else None,
            "plan_start_date": row["plan_start_date"] if is_project else None,
            "scd_end_date": row["scd_end_date"] if is_project else None,
            "last_recalc_date": row["last_recalc_date"] if is_project else None,
        })

    return sorted(rows, key=lambda r: (r["eps_path"], r["node_type"]))


def get_projects_by_eps(eps_name: str | None = None, include_descendants: bool = True) -> dict:
    """Return projects under an EPS node, or a project count summary by EPS path.

    Args:
        eps_name: EPS name, short name, or partial path. If omitted, returns
            project counts grouped by immediate EPS path.
        include_descendants: Include projects under child EPS nodes.
    """
    nodes = _project_nodes()
    paths = _build_paths(nodes)
    by_parent: dict[int | None, list[dict]] = {}
    for row in nodes:
        by_parent.setdefault(row.get("parent_wbs_id"), []).append(row)

    projects = [row for row in nodes if row.get("project_flag") == "Y"]

    if not eps_name:
        counts: dict[str, int] = {}
        for project in projects:
            parent_path = paths.get(project.get("parent_wbs_id"), "(no EPS parent)")
            counts[parent_path] = counts.get(parent_path, 0) + 1
        return {
            "project_count": len(projects),
            "eps_summary": [
                {"eps_path": path, "project_count": count}
                for path, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            ],
        }

    needle = eps_name.lower()
    eps_matches = [
        row for row in nodes
        if row.get("project_flag") != "Y"
        and (
            needle in (row.get("wbs_name") or "").lower()
            or needle in (row.get("wbs_short_name") or "").lower()
            or needle in paths.get(row["wbs_id"], "").lower()
        )
    ]

    if not eps_matches:
        candidates = [
            {"wbs_id": row["wbs_id"], "eps_path": paths.get(row["wbs_id"], "")}
            for row in nodes
            if row.get("project_flag") != "Y"
        ][:25]
        return {
            "error": f"No EPS node matched '{eps_name}'.",
            "sample_eps_nodes": candidates,
        }

    selected_ids: set[int] = set()

    def collect_descendants(wbs_id: int):
        selected_ids.add(wbs_id)
        if not include_descendants:
            return
        for child in by_parent.get(wbs_id, []):
            collect_descendants(child["wbs_id"])

    for match in eps_matches:
        collect_descendants(match["wbs_id"])

    matched_projects = [
        project for project in projects
        if project.get("parent_wbs_id") in selected_ids
        or (include_descendants and project["wbs_id"] in selected_ids)
    ]

    result_projects = []
    for project in matched_projects:
        result_projects.append({
            "proj_id": project["proj_id"],
            "proj_short_name": project["proj_short_name"],
            "project_name": project["wbs_name"],
            "eps_path": paths.get(project.get("parent_wbs_id"), None),
            "plan_start_date": project["plan_start_date"],
            "scd_end_date": project["scd_end_date"],
            "last_recalc_date": project["last_recalc_date"],
            "description": project["description"],
        })

    return {
        "matched_eps_nodes": [
            {"wbs_id": row["wbs_id"], "eps_path": paths.get(row["wbs_id"], "")}
            for row in eps_matches
        ],
        "project_count": len(result_projects),
        "projects": sorted(result_projects, key=lambda r: (r["eps_path"] or "", r["proj_short_name"] or "")),
    }


def find_project(search: str, limit: int = 20, prefer_latest: bool = True) -> list[dict]:
    """Find projects by code, project name, description, or EPS path.

    Args:
        search: Search text such as "CCHS latest" or "EMC May update".
        limit: Maximum matches to return.
        prefer_latest: Boost projects with newer data dates when ranking.
    """
    limit = max(1, min(int(limit), 100))
    nodes = _project_nodes()
    paths = _build_paths(nodes)
    projects = [row for row in nodes if row.get("project_flag") == "Y"]

    raw = search.strip().lower()
    tokens = [
        token for token in re.split(r"[^a-z0-9]+", raw)
        if token and token not in {"latest", "current", "project", "schedule", "update"}
    ]
    wants_latest = prefer_latest or any(word in raw for word in ("latest", "current", "newest"))

    matches = []
    for project in projects:
        parent_path = paths.get(project.get("parent_wbs_id"), "")
        haystack = " ".join([
            project.get("proj_short_name") or "",
            project.get("wbs_name") or "",
            project.get("description") or "",
            parent_path,
        ]).lower()

        score = 0
        short = (project.get("proj_short_name") or "").lower()
        name = (project.get("wbs_name") or "").lower()
        if raw and raw == short:
            score += 100
        if raw and raw == name:
            score += 90
        if raw and short.startswith(raw):
            score += 70
        if raw and name.startswith(raw):
            score += 60
        for token in tokens:
            if token in short:
                score += 25
            elif token in name:
                score += 20
            elif token in haystack:
                score += 8

        if not tokens and raw in haystack:
            score += 10

        if score <= 0:
            continue

        matches.append({
            "match_score": score,
            "proj_id": project["proj_id"],
            "proj_short_name": project["proj_short_name"],
            "project_name": project["wbs_name"],
            "eps_path": parent_path or None,
            "plan_start_date": project["plan_start_date"],
            "scd_end_date": project["scd_end_date"],
            "last_recalc_date": project["last_recalc_date"],
            "description": project["description"],
        })

    def sort_key(row: dict):
        latest_key = row.get("last_recalc_date") or "" if wants_latest else ""
        return (row["match_score"], latest_key, row.get("scd_end_date") or "")

    return sorted(matches, key=sort_key, reverse=True)[:limit]

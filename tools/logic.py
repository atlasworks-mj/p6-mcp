from db import query
from tools.common import schedule_task_type_condition
from tools.paths import get_longest_path


def get_open_ends(project_id: int, include_completed: bool = False) -> dict:
    """Return activities with no predecessors (open starts) or no successors (open finishes).

    Args:
        project_id: The proj_id from get_project_list.
        include_completed: Include completed activities in open-end checks.
    """
    status_clause = "" if include_completed else "AND t.status_code <> 'TK_Complete'"
    task_type_clause = schedule_task_type_condition("t")

    open_starts = query(f"""
        SELECT t.task_code, t.task_name, t.task_type, t.status_code
        FROM TASK t
        WHERE t.proj_id = ?
          AND {task_type_clause}
          {status_clause}
          AND NOT EXISTS (
              SELECT 1 FROM TASKPRED tp WHERE tp.task_id = t.task_id
          )
        ORDER BY t.task_code
    """, (project_id,))

    open_finishes = query(f"""
        SELECT t.task_code, t.task_name, t.task_type, t.status_code
        FROM TASK t
        WHERE t.proj_id = ?
          AND {task_type_clause}
          {status_clause}
          AND NOT EXISTS (
              SELECT 1 FROM TASKPRED tp WHERE tp.pred_task_id = t.task_id
          )
        ORDER BY t.task_code
    """, (project_id,))

    return {
        "open_starts": open_starts,
        "open_start_count": len(open_starts),
        "open_finishes": open_finishes,
        "open_finish_count": len(open_finishes),
        "include_completed": include_completed,
    }


def get_driving_path(project_id: int) -> dict:
    """Return the project-level P6 driving/longest path.

    Uses the same P6-first method as get_longest_path: TASK.driving_path_flag,
    then P6 float path metadata, then minimum total float only as a fallback.
    Relationship fields are added where path activities are linked to each
    other.

    Args:
        project_id: The proj_id from get_project_list.
    """
    result = get_longest_path(project_id=project_id)
    path = result.get("path", [])
    if not path:
        return {
            **result,
            "chain_length": 0,
            "relationship_note": "No P6 driving path activities were found.",
        }

    ids = [row["task_id"] for row in path if row.get("task_id") is not None]
    path_index = {task_id: index for index, task_id in enumerate(ids)}
    placeholders = ",".join("?" for _ in ids)
    rels = query(f"""
        SELECT
            tp.task_id AS successor_id,
            tp.pred_task_id AS predecessor_id,
            tp.pred_type,
            tp.lag_hr_cnt AS lag_hours,
            ROUND(tp.lag_hr_cnt / 8.0, 1) AS lag_days_8h
        FROM TASKPRED tp
        WHERE tp.task_id IN ({placeholders})
          AND tp.pred_task_id IN ({placeholders})
    """, tuple(ids + ids)) if ids else []

    predecessors_by_successor: dict[int, list[dict]] = {}
    for rel in rels:
        predecessors_by_successor.setdefault(rel["successor_id"], []).append(rel)

    by_id = {row["task_id"]: row for row in path}
    enriched_path = []
    for row in path:
        predecessor_links = sorted(
            predecessors_by_successor.get(row["task_id"], []),
            key=lambda rel: path_index.get(rel["predecessor_id"], 999999),
        )
        selected = predecessor_links[-1] if predecessor_links else None
        driven_by = by_id.get(selected["predecessor_id"]) if selected else None
        enriched = dict(row)
        enriched["driven_by"] = driven_by.get("task_code") if driven_by else None
        enriched["relationship"] = selected.get("pred_type") if selected else None
        enriched["lag_hours"] = selected.get("lag_hours") if selected else None
        enriched["lag_days_8h"] = selected.get("lag_days_8h") if selected else None
        enriched["linked_path_predecessors"] = [
            {
                "task_code": by_id.get(link["predecessor_id"], {}).get("task_code"),
                "relationship": link.get("pred_type"),
                "lag_hours": link.get("lag_hours"),
                "lag_days_8h": link.get("lag_days_8h"),
            }
            for link in predecessor_links
        ]
        enriched_path.append(enriched)

    return {
        **result,
        "chain_length": len(enriched_path),
        "relationship_note": "Relationship fields are context only; the path itself comes from P6 task-level path fields or the labeled fallback method.",
        "path": enriched_path,
    }

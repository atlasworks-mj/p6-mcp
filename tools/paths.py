from collections import deque
from datetime import date, datetime, time, timedelta
import re

from db import query, query_single
from tools.common import (
    get_project_settings,
    project_hours_per_day,
    schedule_task_type_condition,
)


_CALENDAR_CACHE: dict[int, dict | None] = {}


def _limit(value: int, default: int = 200, maximum: int = 500) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _chain_limit(value: int, default: int = 1000, maximum: int = 5000) -> int:
    return _limit(value, default=default, maximum=maximum)


def _status_clause(include_completed: bool) -> str:
    return "" if include_completed else "AND t.status_code <> 'TK_Complete'"


def _format_dt(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _as_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time())
    if isinstance(value, str):
        value = value.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, fmt)
                return parsed
            except ValueError:
                pass
    return None


def _schedule_start_sort_value(row: dict) -> datetime:
    for key in ("early_start", "planned_start", "actual_start", "predecessor_endpoint_date"):
        parsed = _as_datetime(row.get(key))
        if parsed:
            return parsed
    return datetime.max


def _schedule_path_sort_key(row: dict) -> tuple:
    return (
        _schedule_start_sort_value(row),
        row.get("task_code") or "",
        row.get("task_name") or "",
    )


def _relationship_gap_sort_key(row: dict) -> tuple:
    gap = row.get("relationship_gap_hours")
    missing_gap = gap is None
    gap_for_sort = gap if gap is not None else float("inf")
    return (
        missing_gap,
        abs(gap_for_sort),
        gap_for_sort,
        _schedule_start_sort_value(row),
        row.get("task_code") or "",
    )


def _hours_per_day(calendar: dict | None) -> float:
    if not calendar:
        return 8.0
    hours = calendar.get("hours_per_day")
    try:
        if hours and float(hours) > 0:
            return float(hours)
    except (TypeError, ValueError):
        pass
    return 8.0


def _project_settings(project_id: int) -> dict | None:
    return get_project_settings(project_id)


def _path_select() -> str:
    return """
        SELECT TOP {top_n}
            t.task_id,
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            w.wbs_name,
            t.clndr_id,
            c.clndr_name,
            ROUND(COALESCE(c.day_hr_cnt, 8), 2) AS calendar_hours_per_day,
            CONVERT(varchar, t.target_start_date, 23) AS planned_start,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            CONVERT(varchar, t.early_start_date, 23) AS early_start,
            CONVERT(varchar, t.early_end_date, 23) AS early_finish,
            t.target_drtn_hr_cnt AS planned_duration_hours,
            t.remain_drtn_hr_cnt AS remaining_duration_hours,
            t.total_float_hr_cnt AS total_float_hours,
            t.free_float_hr_cnt AS free_float_hours,
            ROUND(t.target_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS planned_duration_days,
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
          AND {task_type_clause}
          {status_clause}
          {extra_clause}
        ORDER BY
            COALESCE(t.float_path, 999999),
            COALESCE(t.float_path_order, 999999),
            t.early_start_date,
            t.target_start_date,
            t.task_code
    """


def _path_count(
    project_id: int,
    task_type_clause: str,
    status_clause: str,
    extra_clause: str,
    extra_params: tuple = (),
) -> int:
    row = query_single(f"""
        SELECT COUNT(*) AS activity_count
        FROM TASK t
        WHERE t.proj_id = ?
          AND {task_type_clause}
          {status_clause}
          {extra_clause}
    """, (project_id, *extra_params))
    return int((row or {}).get("activity_count") or 0)


def _load_tasks(project_id: int) -> dict[int, dict]:
    rows = query(f"""
        SELECT
            t.task_id,
            t.proj_id,
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            w.wbs_name,
            t.clndr_id,
            c.clndr_name,
            c.base_clndr_id,
            ROUND(COALESCE(c.day_hr_cnt, 8), 2) AS calendar_hours_per_day,
            CAST(c.clndr_data AS varchar(max)) AS calendar_data,
            t.target_start_date AS planned_start_dt,
            t.target_end_date AS planned_finish_dt,
            t.early_start_date AS early_start_dt,
            t.early_end_date AS early_finish_dt,
            t.late_start_date AS late_start_dt,
            t.late_end_date AS late_finish_dt,
            t.act_start_date AS actual_start_dt,
            t.act_end_date AS actual_finish_dt,
            t.restart_date AS remaining_start_dt,
            t.reend_date AS remaining_finish_dt,
            CONVERT(varchar, t.target_start_date, 23) AS planned_start,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            CONVERT(varchar, t.early_start_date, 23) AS early_start,
            CONVERT(varchar, t.early_end_date, 23) AS early_finish,
            CONVERT(varchar, t.act_start_date, 23) AS actual_start,
            CONVERT(varchar, t.act_end_date, 23) AS actual_finish,
            CONVERT(varchar, t.restart_date, 23) AS remaining_start,
            CONVERT(varchar, t.reend_date, 23) AS remaining_finish,
            t.target_drtn_hr_cnt AS planned_duration_hours,
            t.remain_drtn_hr_cnt AS remaining_duration_hours,
            t.total_float_hr_cnt AS total_float_hours,
            t.free_float_hr_cnt AS free_float_hours,
            ROUND(t.target_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS planned_duration_days,
            ROUND(t.remain_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_duration_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days,
            ROUND(t.free_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS free_float_days,
            t.driving_path_flag,
            t.float_path,
            t.float_path_order
        FROM TASK t
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE (
              t.proj_id = ?
              OR t.task_id IN (
                  SELECT tp.pred_task_id
                  FROM TASKPRED tp
                  JOIN TASK s ON s.task_id = tp.task_id
                  WHERE s.proj_id = ?
              )
          )
          AND {schedule_task_type_condition("t")}
    """, (project_id, project_id))
    return {row["task_id"]: row for row in rows}


def _load_relationships(project_id: int) -> list[dict]:
    return query("""
        SELECT
            tp.task_id AS successor_id,
            tp.pred_task_id AS predecessor_id,
            tp.pred_proj_id AS predecessor_project_id,
            tp.pred_type,
            tp.lag_hr_cnt AS lag_hours,
            ROUND(tp.lag_hr_cnt / 8.0, 1) AS lag_days_8h
        FROM TASKPRED tp
        JOIN TASK s ON s.task_id = tp.task_id
        WHERE s.proj_id = ?
    """, (project_id,))


def _load_project_calendar(project_id: int) -> dict | None:
    row = query_single("""
        SELECT
            c.clndr_id
        FROM PROJECT p
        LEFT JOIN CALENDAR c ON c.clndr_id = p.clndr_id
        WHERE p.proj_id = ?
    """, (project_id,))
    return _load_calendar_by_id(row.get("clndr_id")) if row and row.get("clndr_id") else None


def _load_calendar_by_id(calendar_id: int | None, seen: set[int] | None = None) -> dict | None:
    if calendar_id is None:
        return None
    try:
        calendar_id = int(calendar_id)
    except (TypeError, ValueError):
        return None
    if calendar_id in _CALENDAR_CACHE:
        return _CALENDAR_CACHE[calendar_id]

    seen = set(seen or set())
    if calendar_id in seen:
        return {
            "calendar_id": calendar_id,
            "calendar_name": None,
            "hours_per_day": 8.0,
            "weekly_periods": {},
            "exceptions": {},
            "parsed_calendar_data": False,
            "calendar_caveats": ["base_calendar_cycle_detected"],
        }

    row = query_single("""
        SELECT
            clndr_id,
            clndr_name,
            base_clndr_id,
            ROUND(COALESCE(day_hr_cnt, 8), 2) AS hours_per_day,
            CAST(clndr_data AS varchar(max)) AS calendar_data
        FROM CALENDAR
        WHERE clndr_id = ?
    """, (calendar_id,))
    if not row:
        _CALENDAR_CACHE[calendar_id] = None
        return None

    base = _load_calendar_by_id(row.get("base_clndr_id"), seen | {calendar_id})
    parsed = _parse_calendar(row, base)
    _CALENDAR_CACHE[calendar_id] = parsed
    return parsed


def _parse_clock(value: str) -> int | None:
    match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", value or "")
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour == 24 and minute == 0:
        return 24 * 60
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def _extract_balanced(text: str, start: int) -> tuple[str, int] | None:
    if start < 0 or start >= len(text) or text[start] != "(":
        return None
    depth = 0
    for idx in range(start, len(text)):
        if text[idx] == "(":
            depth += 1
        elif text[idx] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:idx], idx
    return None


def _extract_payload(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        return ""
    start = idx + len(marker)
    if start >= len(text) or text[start] != "(":
        start = text.find("(", start)
    result = _extract_balanced(text, start)
    return result[0] if result else ""


def _iter_top_entries(payload: str) -> list[str]:
    entries = []
    idx = 0
    while idx < len(payload):
        start = payload.find("(0||", idx)
        if start < 0:
            break
        result = _extract_balanced(payload, start)
        if not result:
            break
        entry, end = result
        entries.append(f"({entry})")
        idx = end + 1
    return entries


def _parse_periods(text: str) -> list[tuple[int, int]]:
    periods = []
    for match in re.finditer(r"(s|f)\|(\d{1,2}:\d{2})\|(s|f)\|(\d{1,2}:\d{2})", text or ""):
        first_label, first_time, second_label, second_time = match.groups()
        if first_label == second_label:
            continue
        values = {first_label: first_time, second_label: second_time}
        start = _parse_clock(values["s"])
        finish = _parse_clock(values["f"])
        if start is None or finish is None:
            continue
        if finish == 0 and start > 0:
            finish = 24 * 60
        elif finish <= start:
            finish += 24 * 60
        periods.append((start, finish))
    return sorted(periods)


def _p6_day(py_date: date) -> int:
    # P6 calendar data uses Sunday=1, Monday=2, ..., Saturday=7.
    return 1 if py_date.weekday() == 6 else py_date.weekday() + 2


def _p6_serial_date(value: int) -> date:
    return date(1899, 12, 30) + timedelta(days=value)


def _parse_calendar(row: dict | None, base_calendar: dict | None = None) -> dict | None:
    if not row:
        return None
    raw = (row.get("calendar_data") or "").replace("\x00", "")
    base_weekly = (base_calendar or {}).get("weekly_periods") or {}
    weekly: dict[int, list[tuple[int, int]]] = {
        day_number: list(base_weekly.get(day_number, [])) for day_number in range(1, 8)
    }
    exceptions: dict[date, list[tuple[int, int]]] = dict(
        (base_calendar or {}).get("exceptions") or {}
    )
    caveats = list((base_calendar or {}).get("calendar_caveats") or [])
    saw_calendar_marker = False

    for day_number in range(1, 8):
        marker = f"(0||{day_number}()"
        if marker in raw:
            saw_calendar_marker = True
            payload = _extract_payload(raw, marker)
            weekly[day_number] = _parse_periods(payload)

    exception_payload = _extract_payload(raw, "(0||Exceptions()")
    for entry in _iter_top_entries(exception_payload):
        match = re.search(r"d\|(\d+)", entry)
        if not match:
            continue
        saw_calendar_marker = True
        exceptions[_p6_serial_date(int(match.group(1)))] = _parse_periods(entry)

    if row.get("base_clndr_id") and not base_calendar:
        caveats.append(f"base_calendar_{row.get('base_clndr_id')}_not_found")

    parsed = saw_calendar_marker or bool(base_calendar and base_calendar.get("parsed_calendar_data"))

    return {
        "calendar_id": row.get("clndr_id"),
        "calendar_name": row.get("clndr_name"),
        "base_calendar_id": row.get("base_clndr_id"),
        "base_calendar_name": (base_calendar or {}).get("calendar_name"),
        "hours_per_day": row.get("hours_per_day") or row.get("calendar_hours_per_day") or 8.0,
        "weekly_periods": weekly,
        "exceptions": exceptions,
        "parsed_calendar_data": parsed,
        "calendar_caveats": caveats,
    }


def _task_calendar(task: dict) -> dict | None:
    loaded = _load_calendar_by_id(task.get("clndr_id"))
    if loaded:
        return loaded
    return _parse_calendar({
        "clndr_id": task.get("clndr_id"),
        "clndr_name": task.get("clndr_name"),
        "base_clndr_id": task.get("base_clndr_id"),
        "hours_per_day": task.get("calendar_hours_per_day"),
        "calendar_data": task.get("calendar_data"),
    })


def _twenty_four_hour_calendar() -> dict:
    return {
        "calendar_id": "24h",
        "calendar_name": "24-hour calendar",
        "hours_per_day": 24.0,
        "weekly_periods": {day_number: [(0, 24 * 60)] for day_number in range(1, 8)},
        "exceptions": {},
        "parsed_calendar_data": True,
        "calendar_caveats": [],
    }


def _calendar_for(
    predecessor: dict,
    successor: dict,
    project_calendar: dict | None,
    calendar_basis: str,
) -> tuple[dict | None, str]:
    basis = (calendar_basis or "successor").lower()
    if basis == "predecessor":
        return _task_calendar(predecessor), "predecessor"
    if basis == "project":
        return project_calendar, "project"
    if basis in {"24h", "twenty_four_hour", "twenty-four-hour"}:
        return _twenty_four_hour_calendar(), "24h"
    return _task_calendar(successor), "successor"


def _periods_for_date(calendar: dict | None, current_date: date) -> list[tuple[int, int]]:
    if not calendar:
        return [(0, 24 * 60)]
    if not calendar.get("parsed_calendar_data"):
        hours = _hours_per_day(calendar)
        return [(8 * 60, int((8 + hours) * 60))]
    exceptions = calendar.get("exceptions") or {}
    if current_date in exceptions:
        return exceptions[current_date]
    weekly = calendar.get("weekly_periods") or {}
    return weekly.get(_p6_day(current_date), [])


def _period_intervals_for_anchor(
    calendar: dict | None,
    anchor_date: date,
) -> list[tuple[datetime, datetime]]:
    day_start = datetime.combine(anchor_date, time())
    intervals = []
    for start_minute, finish_minute in _periods_for_date(calendar, anchor_date):
        intervals.append((
            day_start + timedelta(minutes=start_minute),
            day_start + timedelta(minutes=finish_minute),
        ))
    return intervals


def _period_intervals_around(
    calendar: dict | None,
    current_date: date,
) -> list[tuple[datetime, datetime]]:
    intervals = (
        _period_intervals_for_anchor(calendar, current_date - timedelta(days=1))
        + _period_intervals_for_anchor(calendar, current_date)
    )
    return sorted(intervals)


def _work_hours_between(calendar: dict | None, start, finish) -> float | None:
    start_dt = _as_datetime(start)
    finish_dt = _as_datetime(finish)
    if not start_dt or not finish_dt:
        return None
    if start_dt == finish_dt:
        return 0.0
    sign = 1.0
    if finish_dt < start_dt:
        start_dt, finish_dt = finish_dt, start_dt
        sign = -1.0

    total = 0.0
    current_date = start_dt.date() - timedelta(days=1)
    while current_date <= finish_dt.date():
        for interval_start, interval_finish in _period_intervals_for_anchor(calendar, current_date):
            overlap_start = max(start_dt, interval_start)
            overlap_finish = min(finish_dt, interval_finish)
            if overlap_finish > overlap_start:
                total += (overlap_finish - overlap_start).total_seconds() / 3600.0
        current_date += timedelta(days=1)
    return sign * total


def _add_work_hours(calendar: dict | None, start, hours: float) -> datetime | None:
    start_dt = _as_datetime(start)
    if not start_dt:
        return None
    try:
        remaining = float(hours or 0)
    except (TypeError, ValueError):
        remaining = 0.0
    if abs(remaining) < 0.0001:
        return start_dt

    if remaining < 0:
        return _subtract_work_hours(calendar, start_dt, abs(remaining))

    current = start_dt
    guard = 0
    while guard < 10000:
        guard += 1
        for interval_start, interval_finish in _period_intervals_around(calendar, current.date()):
            slot_start = max(current, interval_start)
            if interval_finish <= slot_start:
                continue
            available = (interval_finish - slot_start).total_seconds() / 3600.0
            if remaining <= available:
                return slot_start + timedelta(hours=remaining)
            remaining -= available
        current = datetime.combine(current.date() + timedelta(days=1), time())
    return start_dt + timedelta(hours=hours)


def _subtract_work_hours(calendar: dict | None, start: datetime, hours: float) -> datetime:
    current = start
    remaining = hours
    guard = 0
    while guard < 10000:
        guard += 1
        for interval_start, interval_finish in reversed(_period_intervals_around(calendar, current.date())):
            slot_finish = min(current, interval_finish)
            if slot_finish <= interval_start:
                continue
            available = (slot_finish - interval_start).total_seconds() / 3600.0
            if remaining <= available:
                return slot_finish - timedelta(hours=remaining)
            remaining -= available
        current = datetime.combine(current.date() - timedelta(days=1), time(23, 59, 59))
    return start - timedelta(hours=hours)


def _date_info(task: dict, endpoint: str, date_basis: str) -> dict:
    requested = (date_basis or "status").lower()
    status = task.get("status_code")

    if requested == "actual":
        keys = [f"actual_{endpoint}_dt", f"early_{endpoint}_dt", f"planned_{endpoint}_dt"]
    elif requested == "planned":
        keys = [f"planned_{endpoint}_dt", f"early_{endpoint}_dt", f"actual_{endpoint}_dt"]
    elif requested == "late":
        keys = [f"late_{endpoint}_dt", f"planned_{endpoint}_dt", f"early_{endpoint}_dt"]
    elif requested == "early":
        keys = [f"early_{endpoint}_dt", f"planned_{endpoint}_dt", f"actual_{endpoint}_dt"]
    else:
        requested = "status"
        if status == "TK_Complete":
            keys = [f"actual_{endpoint}_dt", f"early_{endpoint}_dt", f"planned_{endpoint}_dt"]
        elif status == "TK_Active":
            if endpoint == "start":
                keys = ["actual_start_dt", "remaining_start_dt", "early_start_dt", "planned_start_dt"]
            else:
                keys = ["remaining_finish_dt", "early_finish_dt", "planned_finish_dt", "actual_finish_dt"]
        else:
            keys = [f"early_{endpoint}_dt", f"planned_{endpoint}_dt", f"actual_{endpoint}_dt"]

    for key in keys:
        if task.get(key) is not None:
            used_basis = key.split("_", 1)[0]
            caveat = None if key == keys[0] else f"requested_{requested}_used_{key}"
            return {
                "value": task[key],
                "source_key": key,
                "requested_basis": requested,
                "used_basis": used_basis,
                "caveat": caveat,
            }

    return {
        "value": None,
        "source_key": None,
        "requested_basis": requested,
        "used_basis": None,
        "caveat": f"no_{endpoint}_date_available",
    }


def _normalize_relationship(value: str | None) -> str:
    rel = (value or "FS").upper()
    if "_" in rel:
        rel = rel.split("_")[-1]
    return rel if rel in {"FS", "SS", "FF", "SF"} else "FS"


def _relationship_gap(
    predecessor: dict,
    successor: dict,
    rel: dict,
    project_calendar: dict | None,
    date_basis: str,
    calendar_basis: str,
    tolerance_hours: float,
) -> dict:
    rel_type = _normalize_relationship(rel.get("pred_type"))
    pred_endpoint = "finish" if rel_type in {"FS", "FF"} else "start"
    succ_endpoint = "start" if rel_type in {"FS", "SS"} else "finish"
    pred_info = _date_info(predecessor, pred_endpoint, date_basis)
    succ_info = _date_info(successor, succ_endpoint, date_basis)
    pred_date = pred_info["value"]
    succ_date = succ_info["value"]
    calendar, resolved_basis = _calendar_for(predecessor, successor, project_calendar, calendar_basis)
    lag_hours = rel.get("lag_hours") or 0.0
    required_date = _add_work_hours(calendar, pred_date, lag_hours)
    gap_hours = _work_hours_between(calendar, required_date, succ_date)
    hours_day = _hours_per_day(calendar)
    gap_days = None if gap_hours is None else round(gap_hours / hours_day, 2)

    if gap_hours is None:
        classification = "unknown"
    elif abs(gap_hours) <= tolerance_hours:
        classification = "driving"
    elif gap_hours < -tolerance_hours:
        classification = "overdriven_or_out_of_sequence"
    elif gap_hours <= hours_day:
        classification = "near_driving"
    else:
        classification = "loose"

    return {
        "relationship": rel.get("pred_type"),
        "normalized_relationship": rel_type,
        "lag_hours": round(float(lag_hours or 0.0), 2),
        "lag_days_calendar": round(float(lag_hours or 0.0) / hours_day, 2),
        "date_basis": date_basis,
        "calendar_basis": resolved_basis,
        "calendar_id": calendar.get("calendar_id") if calendar else None,
        "calendar_name": calendar.get("calendar_name") if calendar else None,
        "calendar_hours_per_day": hours_day,
        "parsed_calendar_data": bool(calendar and calendar.get("parsed_calendar_data")),
        "calendar_caveats": (calendar or {}).get("calendar_caveats") or [],
        "predecessor_endpoint": pred_endpoint,
        "predecessor_endpoint_date": _format_dt(pred_date),
        "predecessor_endpoint_date_source": pred_info["source_key"],
        "predecessor_endpoint_date_caveat": pred_info["caveat"],
        "successor_endpoint": succ_endpoint,
        "successor_endpoint_date": _format_dt(succ_date),
        "successor_endpoint_date_source": succ_info["source_key"],
        "successor_endpoint_date_caveat": succ_info["caveat"],
        "date_caveats": [c for c in (pred_info["caveat"], succ_info["caveat"]) if c],
        "required_successor_date_from_relationship": _format_dt(required_date),
        "relationship_gap_hours": None if gap_hours is None else round(gap_hours, 2),
        "relationship_gap_days": gap_days,
        "relationship_tightness": classification,
    }


def _relationship_rows_for_successor(
    successor: dict,
    rels: list[dict],
    tasks: dict[int, dict],
    project_calendar: dict | None,
    date_basis: str,
    calendar_basis: str,
    tolerance_hours: float,
    include_completed: bool,
) -> list[dict]:
    rows = []
    for rel in rels:
        predecessor = tasks.get(rel["predecessor_id"])
        if not predecessor:
            continue
        if not include_completed and predecessor.get("status_code") == "TK_Complete":
            continue
        gap = _relationship_gap(
            predecessor,
            successor,
            rel,
            project_calendar,
            date_basis,
            calendar_basis,
            tolerance_hours,
        )
        rows.append({
            "predecessor_id": predecessor["task_id"],
            "successor_id": successor["task_id"],
            "task_code": predecessor["task_code"],
            "task_name": predecessor["task_name"],
            "task_type": predecessor["task_type"],
            "status_code": predecessor["status_code"],
            "wbs_name": predecessor["wbs_name"],
            "planned_start": predecessor["planned_start"],
            "planned_finish": predecessor["planned_finish"],
            "early_start": predecessor["early_start"],
            "early_finish": predecessor["early_finish"],
            "actual_start": predecessor["actual_start"],
            "actual_finish": predecessor["actual_finish"],
            "remaining_start": predecessor["remaining_start"],
            "remaining_finish": predecessor["remaining_finish"],
            "remaining_duration_hours": predecessor["remaining_duration_hours"],
            "total_float_hours": predecessor["total_float_hours"],
            "free_float_hours": predecessor["free_float_hours"],
            "remaining_duration_days": predecessor["remaining_duration_days"],
            "total_float_days": predecessor["total_float_days"],
            "free_float_days": predecessor["free_float_days"],
            "float_path": predecessor["float_path"],
            "float_path_order": predecessor["float_path_order"],
            "driving_path_flag": predecessor["driving_path_flag"],
            "drives_task_code": successor["task_code"],
            **gap,
        })

    known = [row for row in rows if row["relationship_gap_hours"] is not None]
    if known:
        best_abs_gap = min(abs(row["relationship_gap_hours"]) for row in known)
        for row in rows:
            gap_hours = row["relationship_gap_hours"]
            row["is_tightest_predecessor"] = (
                gap_hours is not None and abs(abs(gap_hours) - best_abs_gap) <= tolerance_hours
            )
    else:
        for row in rows:
            row["is_tightest_predecessor"] = False

    return sorted(
        rows,
        key=_relationship_gap_sort_key,
    )


def get_longest_path(
    project_id: int,
    float_path: int | None = None,
    include_completed: bool = False,
    top_n: int = 1000,
) -> dict:
    """Return the P6-calculated longest path activities in path order.

    Uses P6's task-level driving_path_flag first, then P6 multiple-float-path
    metadata, then falls back to minimum total float only when path metadata is
    absent.
    """
    top_n = _chain_limit(top_n)
    status_clause = _status_clause(include_completed)
    task_type_clause = schedule_task_type_condition("t")
    project = _project_settings(project_id)
    method = ""
    confidence = "p6_calculated"
    available_activity_count = 0

    if float_path is not None:
        extra_clause = "AND t.float_path = ?"
        extra_params = (int(float_path),)
        available_activity_count = _path_count(
            project_id, task_type_clause, status_clause, extra_clause, extra_params
        )
        sql = _path_select().format(
            top_n=top_n,
            task_type_clause=task_type_clause,
            status_clause=status_clause,
            extra_clause=extra_clause,
        )
        rows = query(sql, (project_id, *extra_params))
        method = f"float_path={float_path}"
    else:
        extra_clause = "AND t.driving_path_flag = 'Y'"
        available_activity_count = _path_count(
            project_id, task_type_clause, status_clause, extra_clause
        )
        sql = _path_select().format(
            top_n=top_n,
            task_type_clause=task_type_clause,
            status_clause=status_clause,
            extra_clause=extra_clause,
        )
        rows = query(sql, (project_id,))
        method = "driving_path_flag"

        if not rows:
            selected_path = query_single(f"""
                SELECT TOP 1 t.float_path
                FROM TASK t
                WHERE t.proj_id = ?
                  AND {task_type_clause}
                  {status_clause}
                  AND t.float_path IS NOT NULL
                ORDER BY CASE WHEN t.float_path = 1 THEN 0 ELSE 1 END, t.float_path
            """, (project_id,))
            if selected_path and selected_path.get("float_path") is not None:
                extra_clause = "AND t.float_path = ?"
                extra_params = (selected_path["float_path"],)
                available_activity_count = _path_count(
                    project_id, task_type_clause, status_clause, extra_clause, extra_params
                )
                sql = _path_select().format(
                    top_n=top_n,
                    task_type_clause=task_type_clause,
                    status_clause=status_clause,
                    extra_clause=extra_clause,
                )
                rows = query(sql, (project_id, *extra_params))
                method = f"float_path={selected_path['float_path']}"

        if not rows:
            min_float = query_single(f"""
                SELECT MIN(t.total_float_hr_cnt) AS min_float_hr_cnt
                FROM TASK t
                WHERE t.proj_id = ?
                  AND {task_type_clause}
                  {status_clause}
            """, (project_id,))
            if min_float and min_float.get("min_float_hr_cnt") is not None:
                extra_clause = "AND t.total_float_hr_cnt = ?"
                extra_params = (min_float["min_float_hr_cnt"],)
                available_activity_count = _path_count(
                    project_id, task_type_clause, status_clause, extra_clause, extra_params
                )
                sql = _path_select().format(
                    top_n=top_n,
                    task_type_clause=task_type_clause,
                    status_clause=status_clause,
                    extra_clause=extra_clause,
                )
                rows = query(sql, (project_id, *extra_params))
                method = "minimum_total_float_activities"
                confidence = "fallback"
            else:
                rows = []
                available_activity_count = 0

    total_remaining = sum(row.get("remaining_duration_days") or 0 for row in rows)
    total_planned = sum(row.get("planned_duration_days") or 0 for row in rows)
    missing_order_count = sum(1 for row in rows if row.get("float_path_order") is None)
    ordering_note = None
    if missing_order_count:
        ordering_note = (
            f"{missing_order_count} returned activities have no P6 float_path_order; "
            "ordering falls back to early/planned start and activity code."
        )
    fallback_note = None
    if confidence == "fallback":
        fallback_note = (
            "No P6 driving_path_flag or float_path data was found; this is a minimum-total-float "
            "activity set, not a P6-calculated path."
        )
    is_truncated = available_activity_count > len(rows)
    limit_note = None
    if is_truncated:
        limit_note = (
            f"Returned {len(rows)} of {available_activity_count} path activities due to top_n={top_n}. "
            "Request a higher top_n for the complete path."
        )

    return {
        "project": project,
        "source_method": method,
        "source_confidence": confidence,
        "source_note": fallback_note,
        "ordering_note": ordering_note,
        "include_completed": include_completed,
        "activity_count": len(rows),
        "returned_activity_count": len(rows),
        "available_activity_count": available_activity_count,
        "is_truncated": is_truncated,
        "limit_note": limit_note,
        "total_planned_duration_days": round(total_planned, 1),
        "total_remaining_duration_days": round(total_remaining, 1),
        "path": rows,
    }


def get_multiple_float_paths(
    project_id: int,
    include_completed: bool = False,
    max_paths: int = 10,
    top_n_per_path: int = 1000,
) -> dict:
    """Return P6 multiple-float-path results grouped by float path number.

    This only reports data P6 has already calculated into TASK.float_path and
    TASK.float_path_order. It does not synthesize multiple float paths.
    """
    max_paths = _limit(max_paths, default=10, maximum=50)
    top_n_per_path = _chain_limit(top_n_per_path, default=1000, maximum=5000)
    status_clause = _status_clause(include_completed)
    task_type_clause = schedule_task_type_condition("t")
    project = _project_settings(project_id)
    hours_per_day = project_hours_per_day(project)

    path_numbers = query(f"""
        SELECT TOP {max_paths}
            t.float_path,
            COUNT(*) AS total_activity_count,
            SUM(CASE WHEN t.task_type IN ('TT_Mile', 'TT_FinMile') THEN 1 ELSE 0 END) AS milestone_count,
            SUM(CASE WHEN t.driving_path_flag = 'Y' THEN 1 ELSE 0 END) AS driving_path_activity_count,
            MIN(t.total_float_hr_cnt) AS minimum_total_float_hours,
            MAX(t.total_float_hr_cnt) AS maximum_total_float_hours,
            ROUND(MIN(t.total_float_hr_cnt) / ?, 1) AS minimum_total_float_days,
            ROUND(MAX(t.total_float_hr_cnt) / ?, 1) AS maximum_total_float_days
        FROM TASK t
        WHERE t.proj_id = ?
          AND {task_type_clause}
          {status_clause}
          AND t.float_path IS NOT NULL
        GROUP BY t.float_path
        ORDER BY t.float_path
    """, (hours_per_day, hours_per_day, project_id))

    if not path_numbers:
        return {
            "project": project,
            "source_method": "float_path/float_path_order",
            "path_count": 0,
            "message": "No P6 multiple float path data found. Recalculate in P6 with multiple float paths enabled.",
            "paths": [],
        }

    paths = []
    for summary in path_numbers:
        path_number = summary["float_path"]
        activities = query(f"""
            SELECT TOP {top_n_per_path}
                t.task_id,
                t.task_code,
                t.task_name,
                t.task_type,
                t.status_code,
                w.wbs_name,
                CONVERT(varchar, t.target_start_date, 23) AS planned_start,
                CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
                CONVERT(varchar, t.early_start_date, 23) AS early_start,
                CONVERT(varchar, t.early_end_date, 23) AS early_finish,
                t.remain_drtn_hr_cnt AS remaining_duration_hours,
                t.total_float_hr_cnt AS total_float_hours,
                t.free_float_hr_cnt AS free_float_hours,
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
              AND {task_type_clause}
              {status_clause}
              AND t.float_path = ?
            ORDER BY COALESCE(t.float_path_order, 999999), t.early_start_date, t.task_code
        """, (project_id, path_number))
        is_truncated = (summary.get("total_activity_count") or 0) > len(activities)
        paths.append({
            "float_path": path_number,
            "total_activity_count": summary["total_activity_count"],
            "milestone_count": summary["milestone_count"],
            "activity_count_returned": len(activities),
            "is_truncated": is_truncated,
            "limit_note": (
                f"Returned {len(activities)} of {summary['total_activity_count']} activities due to "
                f"top_n_per_path={top_n_per_path}."
                if is_truncated else None
            ),
            "driving_path_activity_count": summary["driving_path_activity_count"],
            "minimum_total_float_hours": summary["minimum_total_float_hours"],
            "maximum_total_float_hours": summary["maximum_total_float_hours"],
            "minimum_total_float_days": summary["minimum_total_float_days"],
            "maximum_total_float_days": summary["maximum_total_float_days"],
            "total_remaining_duration_days": round(
                sum(row.get("remaining_duration_days") or 0 for row in activities), 1
            ),
            "activities": activities,
        })

    return {
        "project": project,
        "source_method": "float_path/float_path_order",
        "include_completed": include_completed,
        "path_count": len(paths),
        "top_n_per_path": top_n_per_path,
        "is_truncated": any(path["is_truncated"] for path in paths),
        "paths": paths,
    }


def get_near_critical_paths(
    project_id: int,
    max_float_days: float = 10,
    include_negative: bool = True,
    top_n: int = 500,
) -> dict:
    """Return incomplete near-critical activities grouped by P6 float path."""
    top_n = _chain_limit(top_n, default=500, maximum=5000)
    project = _project_settings(project_id)
    hours_per_day = project_hours_per_day(project)
    task_type_clause = schedule_task_type_condition("t")
    try:
        max_float_days = float(max_float_days)
    except (TypeError, ValueError):
        max_float_days = 10.0

    lower_clause = "" if include_negative else "AND t.total_float_hr_cnt >= 0"
    max_float_hours = max_float_days * hours_per_day
    matched = query_single(f"""
        SELECT COUNT(*) AS activity_count
        FROM TASK t
        WHERE t.proj_id = ?
          AND {task_type_clause}
          AND t.status_code <> 'TK_Complete'
          AND t.total_float_hr_cnt <= ?
          {lower_clause}
    """, (project_id, max_float_hours))
    matched_activity_count = int((matched or {}).get("activity_count") or 0)

    rows = query(f"""
        SELECT TOP {top_n}
            t.task_id,
            t.task_code,
            t.task_name,
            t.task_type,
            t.status_code,
            w.wbs_name,
            CONVERT(varchar, t.target_start_date, 23) AS planned_start,
            CONVERT(varchar, t.target_end_date, 23) AS planned_finish,
            t.remain_drtn_hr_cnt AS remaining_duration_hours,
            t.total_float_hr_cnt AS total_float_hours,
            ROUND(t.remain_drtn_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS remaining_duration_days,
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1) AS total_float_days,
            t.float_path,
            t.float_path_order,
            t.driving_path_flag
        FROM TASK t
        LEFT JOIN PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN CALENDAR c ON c.clndr_id = t.clndr_id
        WHERE t.proj_id = ?
          AND {task_type_clause}
          AND t.status_code <> 'TK_Complete'
          AND t.total_float_hr_cnt <= ?
          {lower_clause}
        ORDER BY
            COALESCE(t.float_path, 999999),
            ROUND(t.total_float_hr_cnt / NULLIF(COALESCE(c.day_hr_cnt, 8), 0), 1),
            COALESCE(t.float_path_order, 999999),
            t.target_start_date,
            t.task_code
    """, (project_id, max_float_hours))

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        key = f"float_path {row['float_path']}" if row.get("float_path") is not None else "unassigned float path"
        grouped.setdefault(key, []).append(row)

    paths = []
    for key, activities in grouped.items():
        min_float = min((a.get("total_float_days") or 0 for a in activities), default=0)
        paths.append({
            "path_key": key,
            "activity_count": len(activities),
            "minimum_float_days": min_float,
            "total_remaining_duration_days": round(
                sum(a.get("remaining_duration_days") or 0 for a in activities), 1
            ),
            "activities": activities,
        })

    return {
        "project": project,
        "basis": "total_float_window_grouped_by_p6_float_path",
        "max_float_days": max_float_days,
        "float_day_conversion_basis": "project_calendar_hours_per_day",
        "project_hours_per_day": hours_per_day,
        "include_negative": include_negative,
        "activity_count": len(rows),
        "returned_activity_count": len(rows),
        "matched_activity_count": matched_activity_count,
        "is_truncated": matched_activity_count > len(rows),
        "limit_note": (
            f"Returned {len(rows)} of {matched_activity_count} near-critical activities due to top_n={top_n}."
            if matched_activity_count > len(rows) else None
        ),
        "path_count": len(paths),
        "paths": sorted(paths, key=lambda p: (p["minimum_float_days"], p["path_key"])),
    }


def get_path_to_milestone(
    project_id: int,
    milestone_task_code: str,
    include_completed: bool = True,
    max_depth: int = 100,
    top_n: int = 5000,
) -> dict:
    """Trace all predecessor logic back from a milestone or target activity."""
    top_n = _chain_limit(top_n, default=5000)
    max_depth = _chain_limit(max_depth, default=100)
    tasks = _load_tasks(project_id)
    target = next((
        row for row in tasks.values()
        if row["proj_id"] == project_id and row["task_code"] == milestone_task_code
    ), None)
    if not target:
        candidates = [
            {"task_code": row["task_code"], "task_name": row["task_name"], "task_type": row["task_type"]}
            for row in tasks.values()
            if milestone_task_code.lower() in row["task_code"].lower()
            or milestone_task_code.lower() in (row["task_name"] or "").lower()
        ][:10]
        return {
            "error": f"Target activity '{milestone_task_code}' was not found in project_id {project_id}.",
            "candidates": candidates,
        }

    by_successor: dict[int, list[dict]] = {}
    for rel in _load_relationships(project_id):
        by_successor.setdefault(rel["successor_id"], []).append(rel)

    path_rows = []
    queue = [(target["task_id"], 0)]
    expanded: set[int] = set()
    truncated_by_row_limit = False
    max_depth_nodes = []

    while queue and len(path_rows) < top_n:
        successor_id, depth = queue.pop(0)
        if successor_id in expanded:
            continue
        successor = tasks.get(successor_id)
        if depth >= max_depth:
            max_depth_nodes.append({
                "task_code": successor["task_code"] if successor else None,
                "task_name": successor["task_name"] if successor else None,
                "depth": depth,
                "reason": "max_depth_reached",
            })
            continue
        expanded.add(successor_id)

        rels = by_successor.get(successor_id, [])
        for rel in rels:
            predecessor = tasks.get(rel["predecessor_id"])
            if not predecessor:
                continue
            if not include_completed and predecessor.get("status_code") == "TK_Complete":
                continue

            next_depth = depth + 1
            path_rows.append({
                "depth_from_target": next_depth,
                "task_code": predecessor["task_code"],
                "task_name": predecessor["task_name"],
                "task_type": predecessor["task_type"],
                "status_code": predecessor["status_code"],
                "wbs_name": predecessor["wbs_name"],
                "planned_start": predecessor["planned_start"],
                "planned_finish": predecessor["planned_finish"],
                "actual_start": predecessor["actual_start"],
                "actual_finish": predecessor["actual_finish"],
                "remaining_start": predecessor["remaining_start"],
                "remaining_finish": predecessor["remaining_finish"],
                "remaining_duration_hours": predecessor["remaining_duration_hours"],
                "total_float_hours": predecessor["total_float_hours"],
                "free_float_hours": predecessor["free_float_hours"],
                "remaining_duration_days": predecessor["remaining_duration_days"],
                "total_float_days": predecessor["total_float_days"],
                "free_float_days": predecessor["free_float_days"],
                "drives_task_code": successor["task_code"] if successor else None,
                "relationship": rel["pred_type"],
                "lag_hours": rel["lag_hours"],
                "lag_days_8h": rel["lag_days_8h"],
            })
            if len(path_rows) >= top_n:
                truncated_by_row_limit = True
                break
            if next_depth < max_depth:
                queue.append((predecessor["task_id"], next_depth))
            else:
                max_depth_nodes.append({
                    "task_code": predecessor["task_code"],
                    "task_name": predecessor["task_name"],
                    "depth": next_depth,
                    "reason": "max_depth_reached",
                })

    trace_complete = not truncated_by_row_limit and not max_depth_nodes and not queue
    truncation_note = None
    if truncated_by_row_limit:
        truncation_note = (
            f"Trace stopped after {len(path_rows)} predecessor rows due to top_n={top_n}. "
            "Request a higher top_n to continue the chain."
        )
    elif max_depth_nodes:
        truncation_note = (
            f"Trace reached max_depth={max_depth} at {len(max_depth_nodes)} node(s). "
            "Request a higher max_depth to continue the chain."
        )

    return {
        "target": {
            key: value for key, value in target.items()
            if not key.endswith("_dt") and key != "calendar_data"
        },
        "include_completed": include_completed,
        "max_depth": max_depth,
        "top_n": top_n,
        "predecessor_count": len(path_rows),
        "trace_complete": trace_complete,
        "truncated_by_row_limit": truncated_by_row_limit,
        "truncated_by_depth": bool(max_depth_nodes),
        "unexpanded_queue_count": len(queue),
        "max_depth_nodes": max_depth_nodes[:20],
        "truncation_note": truncation_note,
        "target_included_in_path": False,
        "sort_basis": "schedule_start_then_activity_code",
        "path": sorted(path_rows, key=_schedule_path_sort_key),
    }


def get_driving_predecessors(
    project_id: int,
    task_code: str,
    max_depth: int = 10,
    top_n: int = 1000,
    date_basis: str = "status",
    calendar_basis: str = "successor",
    include_completed: bool = False,
    tolerance_hours: float = 1.0,
) -> dict:
    """Return driving predecessor candidates using calendar-aware relationship gaps.

    Relationship gap is calculated from the predecessor endpoint plus lag to the
    successor endpoint, using the selected work calendar basis. The tightest
    predecessor(s), especially zero-gap relationships, are the likely drivers.
    """
    top_n = _chain_limit(top_n)
    max_depth = _chain_limit(max_depth, default=10)
    tasks = _load_tasks(project_id)
    project_calendar = _load_project_calendar(project_id)
    target = next((
        row for row in tasks.values()
        if row["proj_id"] == project_id and row["task_code"] == task_code
    ), None)
    if not target:
        return {"error": f"Activity '{task_code}' was not found in project_id {project_id}."}

    by_successor: dict[int, list[dict]] = {}
    for rel in _load_relationships(project_id):
        by_successor.setdefault(rel["successor_id"], []).append(rel)

    rows = []
    queue = deque([(target["task_id"], 0)])
    visited: set[tuple[int, int]] = set()
    truncated_by_row_limit = False
    max_depth_nodes = []

    while queue and len(rows) < top_n:
        successor_id, depth = queue.popleft()
        if depth >= max_depth:
            successor = tasks.get(successor_id)
            max_depth_nodes.append({
                "task_code": successor["task_code"] if successor else None,
                "task_name": successor["task_name"] if successor else None,
                "depth": depth,
                "reason": "max_depth_reached",
            })
            continue
        successor = tasks.get(successor_id)
        if not successor:
            continue
        rel_rows = _relationship_rows_for_successor(
            successor,
            by_successor.get(successor_id, []),
            tasks,
            project_calendar,
            date_basis,
            calendar_basis,
            tolerance_hours,
            include_completed,
        )
        for rel_row in rel_rows:
            edge_key = (rel_row["predecessor_id"], successor_id)
            if edge_key in visited:
                continue
            visited.add(edge_key)
            rel_row["depth"] = depth + 1
            rows.append(rel_row)
            if len(rows) >= top_n:
                truncated_by_row_limit = True
                break
            if depth + 1 < max_depth:
                queue.append((rel_row["predecessor_id"], depth + 1))
            else:
                max_depth_nodes.append({
                    "task_code": rel_row["task_code"],
                    "task_name": rel_row["task_name"],
                    "depth": depth + 1,
                    "reason": "max_depth_reached",
                })

    safe_target = {
        key: value for key, value in target.items()
        if not key.endswith("_dt") and key != "calendar_data"
    }
    trace_complete = not truncated_by_row_limit and not max_depth_nodes and not queue
    truncation_note = None
    if truncated_by_row_limit:
        truncation_note = (
            f"Candidate predecessor trace stopped after {len(rows)} rows due to top_n={top_n}. "
            "Request a higher top_n or use get_driving_path_to_activity for a focused driver chain."
        )
    elif max_depth_nodes:
        truncation_note = (
            f"Candidate predecessor trace reached max_depth={max_depth} at {len(max_depth_nodes)} node(s). "
            "Request a higher max_depth to continue the chain."
        )
    return {
        "target": safe_target,
        "method": "inferred_calendar_aware_relationship_gap",
        "method_note": "P6 does not expose a relationship-level driving flag; predecessor driving is inferred from relationship endpoint dates, lag, and calendars.",
        "date_source_policy": "status uses actuals for complete work, actual/remaining dates for in-progress work, and early/planned dates for not-started work.",
        "date_basis": date_basis,
        "calendar_basis": calendar_basis,
        "calendar_parse_caveats": (project_calendar or {}).get("calendar_caveats") or [],
        "include_completed": include_completed,
        "tolerance_hours": tolerance_hours,
        "max_depth": max_depth,
        "top_n": top_n,
        "predecessor_count": len(rows),
        "trace_complete": trace_complete,
        "truncated_by_row_limit": truncated_by_row_limit,
        "truncated_by_depth": bool(max_depth_nodes),
        "unexpanded_queue_count": len(queue),
        "max_depth_nodes": max_depth_nodes[:20],
        "truncation_note": truncation_note,
        "candidate_driving_predecessors": sorted(
            rows,
            key=lambda r: (r["depth"], *_relationship_gap_sort_key(r)),
        ),
    }


def get_driving_path_to_activity(
    project_id: int,
    task_code: str,
    max_depth: int = 100,
    top_n: int = 1000,
    date_basis: str = "status",
    calendar_basis: str = "successor",
    include_completed: bool = False,
    tolerance_hours: float = 1.0,
) -> dict:
    """Trace the likely driving logic path backward from one activity.

    At each activity, this walks only the tightest predecessor relationship(s)
    based on calendar-aware relationship gap. Multiple equal drivers are kept as
    branches.
    """
    top_n = _chain_limit(top_n)
    max_depth = _chain_limit(max_depth, default=100)
    tasks = _load_tasks(project_id)
    project_calendar = _load_project_calendar(project_id)
    target = next((
        row for row in tasks.values()
        if row["proj_id"] == project_id and row["task_code"] == task_code
    ), None)
    if not target:
        candidates = [
            {"task_code": row["task_code"], "task_name": row["task_name"], "task_type": row["task_type"]}
            for row in tasks.values()
            if task_code.lower() in row["task_code"].lower()
            or task_code.lower() in (row["task_name"] or "").lower()
        ][:10]
        return {
            "error": f"Activity '{task_code}' was not found in project_id {project_id}.",
            "candidates": candidates,
        }

    by_successor: dict[int, list[dict]] = {}
    for rel in _load_relationships(project_id):
        by_successor.setdefault(rel["successor_id"], []).append(rel)

    path_rows = []
    queue = deque([(target["task_id"], 0)])
    visited_edges: set[tuple[int, int]] = set()
    terminal_nodes = []
    truncated_by_row_limit = False
    max_depth_nodes = []

    while queue and len(path_rows) < top_n:
        successor_id, depth = queue.popleft()
        if depth >= max_depth:
            successor = tasks.get(successor_id)
            max_depth_nodes.append({
                "task_code": successor["task_code"] if successor else None,
                "task_name": successor["task_name"] if successor else None,
                "reason": "max_depth_reached",
                "depth": depth,
            })
            continue
        successor = tasks.get(successor_id)
        if not successor:
            continue
        rel_rows = _relationship_rows_for_successor(
            successor,
            by_successor.get(successor_id, []),
            tasks,
            project_calendar,
            date_basis,
            calendar_basis,
            tolerance_hours,
            include_completed,
        )
        selected = [row for row in rel_rows if row["is_tightest_predecessor"]]
        if not selected and rel_rows:
            selected = rel_rows[:1]
        if not selected:
            terminal_nodes.append({
                "task_code": successor["task_code"],
                "task_name": successor["task_name"],
                "reason": "no_predecessors_found",
                "depth": depth,
            })
            continue

        for rel_row in selected:
            edge_key = (rel_row["predecessor_id"], successor_id)
            if edge_key in visited_edges:
                continue
            visited_edges.add(edge_key)
            rel_row["depth_from_target"] = depth + 1
            path_rows.append(rel_row)
            if len(path_rows) >= top_n:
                truncated_by_row_limit = True
                break
            if depth + 1 < max_depth:
                queue.append((rel_row["predecessor_id"], depth + 1))
            else:
                max_depth_nodes.append({
                    "task_code": rel_row["task_code"],
                    "task_name": rel_row["task_name"],
                    "reason": "max_depth_reached",
                    "depth": depth + 1,
                })

    safe_target = {
        key: value for key, value in target.items()
        if not key.endswith("_dt") and key != "calendar_data"
    }
    trace_complete = not truncated_by_row_limit and not max_depth_nodes and not queue
    truncation_note = None
    if truncated_by_row_limit:
        truncation_note = (
            f"Driving path trace stopped after {len(path_rows)} relationship rows due to top_n={top_n}. "
            "Request a higher top_n to continue the chain."
        )
    elif max_depth_nodes:
        truncation_note = (
            f"Driving path trace reached max_depth={max_depth} at {len(max_depth_nodes)} node(s). "
            "Request a higher max_depth to continue the chain."
        )
    return {
        "target": safe_target,
        "method": "inferred_recursive_calendar_aware_relationship_gap",
        "method_note": "P6 does not expose a relationship-level driving flag; this trace is inferred from the tightest predecessor relationship at each step.",
        "date_source_policy": "status uses actuals for complete work, actual/remaining dates for in-progress work, and early/planned dates for not-started work.",
        "date_basis": date_basis,
        "calendar_basis": calendar_basis,
        "calendar_parse_caveats": (project_calendar or {}).get("calendar_caveats") or [],
        "include_completed": include_completed,
        "tolerance_hours": tolerance_hours,
        "max_depth": max_depth,
        "top_n": top_n,
        "path_edge_count": len(path_rows),
        "trace_complete": trace_complete,
        "truncated_by_row_limit": truncated_by_row_limit,
        "truncated_by_depth": bool(max_depth_nodes),
        "unexpanded_queue_count": len(queue),
        "sort_basis": "schedule_start_then_activity_code",
        "terminal_nodes": terminal_nodes,
        "max_depth_nodes": max_depth_nodes[:20],
        "truncation_note": truncation_note,
        "driving_path": sorted(path_rows, key=_schedule_path_sort_key),
    }

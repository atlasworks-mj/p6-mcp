# P6 MCP Tool Guide

## Operating Rules

- Use the MCP tools directly when available. Do not infer database facts from
  memory.
- Keep all database access read-only. Never suggest write SQL.
- Prefer dedicated tools over `query_schedule`.
- Use exact `project_id` once known. P6 task, WBS, and resource IDs are often
  version-local; use activity codes like `TASK.task_code` when comparing
  schedule versions.
- Call out when a result depends on populated P6-calculated fields such as
  critical flags, driving path flags, float paths, early/late dates, resource
  assignments, or cost records.
- For P6 Professional SQL Server, this MCP reads the native database schema.
  EPPM/OPC implementations may require separate API-oriented tools.

## Start Here

| User intent | First tool | Follow-up |
|---|---|---|
| "What projects are in P6?" | `get_project_list` | `get_eps_tree` for hierarchy |
| "Find the May 2012 update" | `find_project` | `get_schedule_summary` |
| "Show EPS" | `get_eps_tree` | `get_projects_by_eps` |
| "Activities matching X" | `search_activities` | `get_activity_detail` |
| "Activity detail" | `get_activity_detail` | path/diagnostic tools as needed |
| "What codes/UDFs are used?" | `get_activity_codes`, `get_udfs` | usage tools |

## Path And Driver Questions

Use these tools carefully because "critical", "longest", and "driving" are
related but not identical concepts.

| Question | Tool | Notes |
|---|---|---|
| "Critical path" | `get_critical_path` | Follows project P6 setting. If P6 uses total float, critical is threshold-based; if driving path, use P6 driving path flag. |
| "Longest path" | `get_longest_path` | Prefer P6 longest/driving fields and float path metadata; fallbacks should be described. |
| "Multiple float paths" | `get_multiple_float_paths` | Only meaningful when P6 calculated multiple float paths. |
| "Near critical" | `get_near_critical_paths` | Use float-path grouping where available. |
| "Path to milestone/activity" | `get_path_to_milestone` | Use for a broad predecessor trace to a target. |
| "What drives this activity?" | `get_driving_predecessors` | Returns candidate drivers using calendar-aware relationship gaps. |
| "Driving path to this activity" | `get_driving_path_to_activity` | Recursively traces likely drivers back from the target. |

Response pattern for path questions:

1. Name the target project and target activity/milestone.
2. Provide the ordered path or top drivers.
3. Mention relationship type, lag, gap, dates, and float when relevant.
4. State whether the path is P6-calculated or inferred.
5. If ordering matters, sort by start/finish dates, then activity ID/name as a
   tie-breaker.

## Baselines, Updates, And Change

| Question | Tool | Notes |
|---|---|---|
| "What baseline is assigned?" | `get_project_baselines` | Shows assigned and likely related baselines. |
| "Find related updates" | `find_related_updates` | Uses baseline links, EPS path, names, and activity overlap. |
| "Compare to baseline" | `compare_to_baseline` | Resolves assigned/original/summary/named/explicit baselines. |
| "Compare two versions" | `compare_schedules` | Summary-level comparison. |
| "Detailed update comparison" | `compare_schedule_updates` | Activity, logic, WBS, and path deltas. |
| "Who/what caused variance?" | `get_change_attribution` | Group by WBS, activity code, resource, or role. |
| "Update timeline" | `get_update_timeline` | Chronological related update view. |
| "WBS baseline variance" | `get_baseline_variance_by_wbs` | WBS rollup of baseline/update deltas. |

When comparing versions, prefer `task_code` as cross-version activity identity.
Warn if matches are low-confidence or baseline links are missing/stale.

## Diagnostics And Schedule Quality

| Question | Tool | Notes |
|---|---|---|
| "Schedule summary" | `get_schedule_summary` | Use before deeper diagnostics. |
| "Analyze this schedule" | `analyze_schedule` | Broad risk/quality scan, including float data quality. |
| "Open ends" | `get_open_ends` | Missing predecessors/successors. |
| "Progress issues" | `get_progress_diagnostics` | Missed starts/finishes, out-of-sequence progress, recent actuals. |
| "Constraints/date control" | `get_constraint_diagnostics` | Constraints and date-control indicators. |
| "Relationship quality" | `get_relationship_diagnostics` | Relationship types, lag, external logic. |
| "WBS progress" | `get_progress_by_wbs` | Rollup by WBS node, not just WBS name. |

Avoid overstating severity. Present diagnostics as flags for scheduler review.

## Resources, Costs, And Calendars

| Question | Tool | Notes |
|---|---|---|
| "Resource loading" | `get_resource_summary` | Group by resource, role, WBS, or type. |
| "Cost summary" | `get_cost_summary` | Depends on populated resource/cost data. |
| "Cash flow" | `get_cash_flow_by_month` | Bucketed by month. |
| "Calendars used" | `get_project_calendars` | Project calendars and usage. |
| "Calendar details" | `get_calendar_detail` | Use `include_raw` only when necessary. |
| "Activities on calendar X" | `get_calendar_activity_usage` | Useful for path/driver caveats. |

Calendar-aware tools parse P6 calendar data best-effort. Mention caveats if base
calendars or raw calendar data are incomplete.

## Custom SQL

Use `query_schedule` only when:

- The question cannot be answered with a dedicated tool.
- The SQL is a single `SELECT` or `WITH ... SELECT`.
- The result can be reasonably capped.

Never use or suggest `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`,
`EXEC`, `MERGE`, `GRANT`, `REVOKE`, `DBCC`, `BACKUP`, `RESTORE`, `XP_CMDSHELL`,
or semicolon-separated statements.

If a user asks for a destructive or corrective database action, refuse the write
operation and offer a read-only diagnostic alternative.

## Useful Answer Shapes

For a direct factual lookup:

- Answer directly.
- Include the project/update context.
- Add one caveat only if the data suggests it.

For diagnostics:

- Lead with highest-risk findings.
- Include counts and representative examples.
- Separate database facts from recommendations.

For path analysis:

- State target and path basis.
- Show the path in chronological order.
- Include dates, relationship types/lags, and float/gap context.

For comparisons:

- State baseline/update IDs and names.
- Summarize major date, logic, WBS, and path changes.
- Include match confidence and missing-data caveats.

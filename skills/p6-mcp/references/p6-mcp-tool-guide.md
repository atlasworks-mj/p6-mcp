# P6 MCP Tool Guide

## Operating Rules

- Use the MCP tools directly when available. Do not infer database facts from
  memory.
- Keep all database access read-only. Never suggest write SQL.
- Prefer dedicated tools over `query_schedule`.
- Use exact `project_id` once known for MCP tool calls. In user-facing answers,
  always name the project and include project code/update label when available;
  show `project_id` only as supporting technical context. P6 task, WBS, and
  resource IDs are often version-local; use activity codes like
  `TASK.task_code` when comparing schedule versions.
- Call out when a result depends on populated P6-calculated fields such as
  critical flags, driving path flags, float paths, early/late dates, resource
  assignments, or cost records.
- Do not let row caps turn a causal explanation into a partial answer. `top_n`
  is appropriate for ranked lists, samples, and broad extracts. For path,
  driver, baseline narrative, and "why did this happen?" questions, use enough
  depth and rows to tell the full story, and check completion metadata before
  answering.
- For P6 Professional SQL Server, this MCP reads the native database schema.
  EPPM/OPC implementations may require separate API-oriented tools.

## Completeness Over Top-N

Use caps as guardrails, not as the shape of the answer.

- For causal chains and paths, prefer `get_driving_path_to_activity`,
  `get_path_to_milestone`, `get_longest_path`, or `get_multiple_float_paths`
  with enough `top_n`, `top_n_per_path`, and `max_depth` to reach a natural
  stopping point.
- Check `trace_complete`, `truncated_by_row_limit`, `truncated_by_depth`,
  `truncation_note`, `is_truncated`, `returned_activity_count`, and
  `available_activity_count` where present.
- If a chain is incomplete, do not present it as the full driver/path. Re-query
  with a higher limit when practical, or explicitly say where the trace stopped.
- For "what is driving X?", walk back to the active/current work when answering
  remaining-work questions. If the user asks for history or cause, include
  completed predecessors and actual dates to explain how the schedule got there.
- For broad diagnostics and comparisons, summarize the most important items but
  say when lists are representative samples rather than the entire population.

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

1. Name the target project and target activity/milestone. Use the project name
   before any internal numeric ID.
2. Provide the ordered path or top drivers.
3. Mention relationship type, lag, gap, dates, and float when relevant.
4. State whether the path is P6-calculated or inferred.
5. Translate the path into practical meaning: what is actually controlling the
   work, where coordination risk sits, and what a scheduler or project team
   should review next.
6. Check trace completion metadata. If a row/depth limit stopped the chain,
   continue the trace or label the answer incomplete.
7. If ordering matters, sort by start/finish dates, then activity ID/name as a
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
Tie each finding to practical project impact where possible, such as missed
starts, unreliable float, constrained work, open logic, update quality, or
activities that need planner/superintendent review.

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
- The result can be reasonably capped without distorting the answer, or the
  answer clearly labels the result as a sample.

Never use or suggest `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`,
`EXEC`, `MERGE`, `GRANT`, `REVOKE`, `DBCC`, `BACKUP`, `RESTORE`, `XP_CMDSHELL`,
or semicolon-separated statements.

If a user asks for a destructive or corrective database action, refuse the write
operation and offer a read-only diagnostic alternative.

## Useful Answer Shapes

For a direct factual lookup:

- Answer directly.
- Include the project/update name or code. Do not make the user decode a bare
  numeric `project_id`.
- Add one caveat only if the data suggests it.

For diagnostics:

- Lead with highest-risk findings.
- Include counts and representative examples.
- Separate database facts from recommendations.

For path analysis:

- State target and path basis.
- Show the path in chronological order and trace it to a natural starting point,
  current active work, or a clearly stated data/logic boundary.
- Include dates, relationship types/lags, and float/gap context.
- Explain the real-world scheduling meaning before going deep on theory.
- State whether the trace is complete. If it is not complete, explain where and
  why it stopped.

For comparisons:

- State baseline/update names and codes first, with internal IDs only as
  supporting context when useful.
- Summarize major date, logic, WBS, and path changes.
- Include match confidence and missing-data caveats.

## Tone And Practicality

Use scheduling theory as a lens, not as the answer. If a distinction such as
total float criticality versus academic longest path matters, explain it briefly
and then state how it affects the schedule review. Prefer language that helps a
project team act: "this predecessor is the likely driver", "this float looks
unreliable because dates are missing", "this WBS has the largest finish
variance", or "this should be reviewed before relying on the path."

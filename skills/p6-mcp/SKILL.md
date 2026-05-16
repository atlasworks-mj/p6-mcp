---
name: p6-mcp
description: Use this skill when working with the P6 MCP server for Primavera P6 schedule data, including questions about EPS/project discovery, activities, WBS, critical path, longest path, driving path, multiple float paths, milestone drivers, baseline/update comparisons, change attribution, schedule quality diagnostics, progress, constraints, relationships, resources, costs, calendars, activity codes, UDFs, or read-only SQL against a P6 Professional SQL Server database.
---

# P6 MCP

Use the `p6` MCP tools to answer Primavera P6 schedule questions from the
database instead of guessing. Treat the MCP as a read-only reporting interface:
never attempt to create, update, delete, or repair P6 records.

## Core Workflow

1. Identify the project first.
   - If the user gives a project/update name, use `find_project`.
   - If they mention EPS, use `get_eps_tree` or `get_projects_by_eps`.
   - If context is missing, use `get_project_list` and ask only when needed.

2. Pick the narrowest useful tool.
   - Use structured tools before `query_schedule`.
   - Use `query_schedule` only for read-only SELECT questions not covered by a
     dedicated tool.
   - Keep `top_n` or result limits bounded unless the user explicitly needs a
     broad extract.

3. Preserve P6 semantics.
   - Critical path should follow the project's P6 critical path setting.
   - Longest path and multiple float paths should prefer P6-calculated fields
     when populated.
   - Driving predecessor/path tools infer relationship tightness from dates,
     lags, relationship types, and calendars; explain that this is inference
     when P6 does not expose a relationship-level driving flag.

4. Answer for real-world schedule use.
   - Always reference the project name, and include project code/update label
     when available. Use `project_id` in tool calls, but treat it as a
     secondary technical identifier in user-facing answers.
   - Report caveats for missing float/path/date/resource/cost data.
   - Sort path results chronologically by start/date order, using activity ID or
     name only as a tie-breaker.
   - Blend scheduling theory with field usefulness: explain critical, longest,
     float, and driving concepts when they matter, then translate them into what
     the result means for coordination, risk, delay, sequencing, or next review.
   - Distinguish data facts from schedule judgment and avoid academic detail
     that does not change the practical answer.

## Frequent Tool Choices

- Project lookup: `find_project`, `get_project_list`, `get_eps_tree`,
  `get_projects_by_eps`
- Activity lookup: `get_activities`, `search_activities`,
  `get_activity_detail`
- Codes/UDFs: `get_activity_codes`, `get_activity_code_usage`, `get_udfs`,
  `get_udf_usage`
- Path questions: `get_critical_path`, `get_longest_path`,
  `get_multiple_float_paths`, `get_near_critical_paths`,
  `get_path_to_milestone`, `get_driving_predecessors`,
  `get_driving_path_to_activity`
- Schedule health: `analyze_schedule`, `get_open_ends`,
  `get_progress_diagnostics`, `get_constraint_diagnostics`,
  `get_relationship_diagnostics`
- Baselines and updates: `get_project_baselines`, `find_related_updates`,
  `compare_to_baseline`, `compare_schedules`, `compare_schedule_updates`,
  `get_change_attribution`, `get_update_timeline`,
  `get_baseline_variance_by_wbs`
- WBS/progress: `get_wbs`, `get_schedule_summary`, `get_progress_by_wbs`
- Resources/costs/calendars: `get_resource_summary`, `get_cost_summary`,
  `get_cash_flow_by_month`, `get_project_calendars`, `get_calendar_detail`,
  `get_calendar_activity_usage`

## Detailed Reference

For tool-by-tool routing, response patterns, and caveats, read
`references/p6-mcp-tool-guide.md` when the request is complex, ambiguous, or
touches path analysis, baselines, diagnostics, or custom SQL.

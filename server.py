from fastmcp import FastMCP
from tools import (
    activities,
    activity_detail,
    activity_metadata,
    analysis,
    baselines,
    calendars,
    change_attribution,
    changes,
    diagnostics,
    eps,
    logic,
    paths,
    progress,
    projects,
    query,
    resources,
    wbs,
)

mcp = FastMCP("P6 Schedule Server")

# Core tools
mcp.tool()(projects.get_project_list)
mcp.tool()(eps.get_eps_tree)
mcp.tool()(eps.get_projects_by_eps)
mcp.tool()(eps.find_project)
mcp.tool()(activities.get_activities)
mcp.tool()(activity_detail.get_activity_detail)
mcp.tool()(activity_metadata.get_activity_codes)
mcp.tool()(activity_metadata.get_activity_code_usage)
mcp.tool()(activity_metadata.get_udfs)
mcp.tool()(activity_metadata.get_udf_usage)
mcp.tool()(activity_metadata.search_activities)
mcp.tool()(activities.get_critical_path)
mcp.tool()(activities.get_slippage)
mcp.tool()(activities.get_procurement_status)

# Logic & structure
mcp.tool()(logic.get_open_ends)
mcp.tool()(logic.get_driving_path)
mcp.tool()(paths.get_longest_path)
mcp.tool()(paths.get_multiple_float_paths)
mcp.tool()(paths.get_near_critical_paths)
mcp.tool()(paths.get_path_to_milestone)
mcp.tool()(paths.get_driving_predecessors)
mcp.tool()(paths.get_driving_path_to_activity)
mcp.tool()(wbs.get_wbs)

# Analysis & comparison
mcp.tool()(analysis.get_schedule_summary)
mcp.tool()(analysis.analyze_schedule)
mcp.tool()(analysis.compare_schedules)
mcp.tool()(changes.compare_schedule_updates)
mcp.tool()(change_attribution.get_change_attribution)
mcp.tool()(baselines.get_project_baselines)
mcp.tool()(baselines.find_related_updates)
mcp.tool()(baselines.compare_to_baseline)
mcp.tool()(baselines.get_update_timeline)
mcp.tool()(baselines.get_baseline_variance_by_wbs)
mcp.tool()(analysis.get_progress_by_wbs)
mcp.tool()(progress.get_progress_diagnostics)
mcp.tool()(diagnostics.get_constraint_diagnostics)
mcp.tool()(diagnostics.get_relationship_diagnostics)

# Resources, costs, and calendars
mcp.tool()(resources.get_resource_summary)
mcp.tool()(resources.get_cost_summary)
mcp.tool()(resources.get_cash_flow_by_month)
mcp.tool()(calendars.get_project_calendars)
mcp.tool()(calendars.get_calendar_detail)
mcp.tool()(calendars.get_calendar_activity_usage)

# Freeform query
mcp.tool()(query.query_schedule)

if __name__ == "__main__":
    mcp.run()

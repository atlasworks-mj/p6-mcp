# P6 MCP Server

A local [Model Context Protocol](https://modelcontextprotocol.io) server for
querying Oracle Primavera P6 Professional schedule data from MCP-compatible AI
clients.

Built and maintained by [Atlas Works, LLC](https://www.theatlasworks.com).

This project is read-only by design. It is intended for schedule analysis,
forensics, comparison, and reporting conversations against a local P6 SQL Server
database. It does not create, update, or delete P6 records.

> This is an independent open source project. It is not affiliated with,
> endorsed by, or sponsored by Oracle, Primavera, or any Oracle affiliate.
> Oracle and Primavera are trademarks of Oracle and/or its affiliates.

## What It Does

Connect any MCP-compatible AI client to your local P6 database and ask schedule
questions in natural language:

- "What projects are in my P6 database?"
- "Show me the critical path for this update."
- "What is driving the final completion milestone?"
- "Compare this update to its baseline."
- "Which WBS areas are slipping?"
- "Give me schedule quality diagnostics."
- "Show open ends, constraints, high lag, and out-of-sequence progress."

The server exposes structured tools for project discovery, activity lookup,
logic tracing, P6 critical/longest path data, baseline comparisons, schedule
diagnostics, activity codes, UDFs, resources, costs, calendars, and capped
read-only SQL.

## Key Capabilities

- EPS and project discovery.
- Activity search by activity ID, name, WBS, activity code, UDF, notes, and
  steps.
- Activity detail with predecessors, successors, resources, costs, notes, and
  UDFs.
- P6-aware critical path and longest path tools.
- P6 multiple float path reporting when P6 has calculated float paths.
- Calendar-aware driving predecessor and driving path inference.
- Baseline/update comparison and change attribution.
- WBS-level progress and variance rollups.
- Progress, relationship, constraint, and date-control diagnostics.
- Resource, cost, and cash-flow summaries where populated in the database.
- Calendar usage and calendar detail tools.
- A capped, read-only `query_schedule` tool for ad hoc SQL Server SELECT
  queries.

## Tools

| Tool | Description |
|---|---|
| `get_project_list` | List all P6 projects with dates and metadata. |
| `get_eps_tree` | Browse the EPS/project tree as flattened hierarchy rows. |
| `get_projects_by_eps` | List projects under an EPS node, or summarize project counts by EPS. |
| `find_project` | Find projects by code, project name, description, or EPS path. |
| `get_activities` | Query activities with WBS, float, status, sorting, and row-limit filters. |
| `get_activity_detail` | Full activity detail, including logic, resources, costs, notes, memos, and UDFs. |
| `get_activity_codes` | List activity code types and values used by a project. |
| `get_activity_code_usage` | Show activities assigned to activity code types and values. |
| `get_udfs` | List task UDF definitions and values used by a project. |
| `get_udf_usage` | Show activities with matching task UDF assignments. |
| `search_activities` | Search activities by core fields, codes, UDFs, and optional notes/steps. |
| `get_critical_path` | Return critical activities using the project's P6 critical path setting. |
| `get_driving_path` | Return the project-level P6 driving/longest path where P6 path data exists. |
| `get_longest_path` | Return P6-calculated longest path data using driving path flag, float path metadata, then minimum-float fallback. |
| `get_multiple_float_paths` | Return P6 multiple float path results grouped by `float_path` and `float_path_order`. |
| `get_near_critical_paths` | Return near-critical activities grouped by P6 float path. |
| `get_path_to_milestone` | Trace predecessor logic back from a target milestone or activity. |
| `get_driving_predecessors` | Return candidate drivers using calendar-aware relationship gap calculations. |
| `get_driving_path_to_activity` | Recursively trace the likely driving path back from one activity. |
| `get_slippage` | Return completed activities that finished late, sorted by worst slippage. |
| `get_procurement_status` | Return buyout/procurement activities sorted by worst float. |
| `get_open_ends` | Return activities missing predecessors or successors. |
| `get_wbs` | Return the WBS hierarchy with activity counts. |
| `get_schedule_summary` | Return high-level schedule metrics. |
| `analyze_schedule` | Return schedule quality analysis with risk flags and float data quality. |
| `compare_schedules` | Compare two schedule versions at a summary level. |
| `compare_schedule_updates` | Compare two schedule versions with activity, logic, WBS, and path deltas. |
| `get_change_attribution` | Attribute schedule variance by WBS, activity code, resource, or role. |
| `get_project_baselines` | Show assigned and likely related P6 baseline projects. |
| `find_related_updates` | Find likely related updates by baseline links, EPS path, name similarity, and activity overlap. |
| `compare_to_baseline` | Resolve an assigned, original, summary, named, or explicit baseline and compare it. |
| `get_update_timeline` | Return a chronological view of likely related updates. |
| `get_baseline_variance_by_wbs` | Compare update and baseline rows and roll variance up by WBS. |
| `get_progress_by_wbs` | Roll up schedule metrics by WBS node. |
| `get_progress_diagnostics` | Return missed starts/finishes, out-of-sequence progress, recent actuals, and update anomalies. |
| `get_constraint_diagnostics` | Return constraint and date-control diagnostics. |
| `get_relationship_diagnostics` | Return relationship type, lag, and external-logic diagnostics. |
| `get_resource_summary` | Summarize labor/resource assignments by resource, role, WBS, or type. |
| `get_cost_summary` | Return resource and project cost totals with WBS rollups. |
| `get_cash_flow_by_month` | Bucket resource and/or cost-item cost by month. |
| `get_project_calendars` | Return calendars used by a project and activity/resource usage counts. |
| `get_calendar_detail` | Return calendar metadata and usage. |
| `get_calendar_activity_usage` | Return project activities with assigned calendars. |
| `query_schedule` | Execute capped, read-only SQL Server SELECT queries against the P6 database. |

## Requirements

- Python 3.11 or newer.
- Oracle Primavera P6 Professional with a SQL Server database backend.
- ODBC Driver 17 or 18 for SQL Server.
- A SQL Server login with read-only access to the P6 database.

This server has been built against SQL Server-backed P6 data. Other P6 database
backends are not currently supported.

### P6 edition and support note

This implementation targets P6 Professional databases with a SQL Server backend.
It does not use the P6 EPPM REST API or EPPM Extended Schema.

P6 EPPM deployments may be better served by Oracle's documented REST API or
Extended Schema reporting views, and a future EPPM-oriented implementation could
adapt these MCP tools around those interfaces.

Direct SQL access to the native P6 Professional schema is useful for local
reporting and analysis, but it may not be an Oracle-supported integration path.
Use only authorized, read-only database credentials and review your own Oracle
license, hosting, and support terms before use.

## Installation

### 1. Create a read-only SQL Server login

In SQL Server Management Studio, connect to your P6 SQL Server instance and run:

```sql
CREATE LOGIN p6_reader WITH PASSWORD = 'UseAStrongPasswordHere';

USE YourP6DatabaseName;

CREATE USER p6_reader FOR LOGIN p6_reader;

ALTER ROLE db_datareader ADD MEMBER p6_reader;
```

Use least-privilege credentials. The MCP server is designed to reject writes at
the application layer, but the database login is the strongest security
boundary.

### 2. Clone and install dependencies

```bash
git clone https://github.com/atlasworks-mj/p6-mcp.git
cd p6-mcp
python -m venv venv
```

On Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS or Linux:

```bash
source venv/bin/activate
python -m pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the example environment file:

```bash
cp .env.example .env
```

On Windows PowerShell, if `cp` is not available:

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

```text
P6_SERVER=YOUR_SERVER\SQLEXPRESS
P6_DATABASE=YOUR_P6_DATABASE
P6_USER=p6_reader
P6_PASSWORD=your_password_here
P6_DRIVER=ODBC Driver 18 for SQL Server
```

### 4. Smoke test the connection

```bash
python -c "from tools.projects import get_project_list; print(get_project_list()[:3])"
```

If you see project rows, the server can read your P6 database.

## Connecting MCP Clients

This server uses MCP stdio transport. Your AI client launches `server.py` as a
subprocess and communicates over stdin/stdout. The server does not open a
network port.

Use absolute paths in client configuration.

### Claude Desktop

Edit your Claude Desktop config file:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

Example:

```json
{
  "mcpServers": {
    "p6": {
      "command": "C:/absolute/path/to/p6-mcp/venv/Scripts/python.exe",
      "args": ["C:/absolute/path/to/p6-mcp/server.py"]
    }
  }
}
```

Restart Claude Desktop after editing the config.

### Claude Code

Add a `.mcp.json` file in your working project:

```json
{
  "mcpServers": {
    "p6": {
      "command": "C:/absolute/path/to/p6-mcp/venv/Scripts/python.exe",
      "args": ["C:/absolute/path/to/p6-mcp/server.py"]
    }
  }
}
```

Or use the CLI:

```bash
claude mcp add p6 C:/absolute/path/to/p6-mcp/venv/Scripts/python.exe C:/absolute/path/to/p6-mcp/server.py
```

### Cursor

Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "p6": {
      "command": "C:/absolute/path/to/p6-mcp/venv/Scripts/python.exe",
      "args": ["C:/absolute/path/to/p6-mcp/server.py"]
    }
  }
}
```

### VS Code with GitHub Copilot

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "p6": {
      "command": "C:/absolute/path/to/p6-mcp/venv/Scripts/python.exe",
      "args": ["C:/absolute/path/to/p6-mcp/server.py"]
    }
  }
}
```

### Codex CLI

```bash
codex mcp add p6 C:/absolute/path/to/p6-mcp/venv/Scripts/python.exe C:/absolute/path/to/p6-mcp/server.py
```

You can verify the server is configured with:

```bash
codex mcp list
```

### Codex Desktop App

Codex Desktop can use a project-local `.mcp.json` file. Add this file to the
folder you open as your Codex project or workspace:

```json
{
  "mcpServers": {
    "p6": {
      "command": "C:/absolute/path/to/p6-mcp/venv/Scripts/python.exe",
      "args": ["C:/absolute/path/to/p6-mcp/server.py"]
    }
  }
}
```

Then reload the Codex project/thread or restart Codex Desktop. In a new thread,
ask Codex to list available P6 projects or call a simple tool such as
`get_project_list`.

Keep `.mcp.json` local. It can contain machine-specific paths, so this
repository's `.gitignore` excludes it by default.

On macOS and Linux, use the virtual environment Python at
`/absolute/path/to/p6-mcp/venv/bin/python`.

### Optional Codex Skill

This repository includes an optional Codex skill at `skills/p6-mcp`. The skill
teaches Codex how to choose between the P6 MCP tools, handle path-analysis
caveats, and answer schedule questions with P6-aware terminology.

To install it locally on Windows PowerShell:

```powershell
Copy-Item -Recurse -Force skills\p6-mcp "$env:USERPROFILE\.codex\skills\"
```

On macOS and Linux:

```bash
mkdir -p ~/.codex/skills
cp -R skills/p6-mcp ~/.codex/skills/
```

Reload Codex or start a new thread after installing the skill.

### Other MCP Clients

Any MCP client that supports stdio transport should work. Configure:

- Command: the Python executable inside this repository's virtual environment.
- Arguments: the absolute path to `server.py`.

On macOS and Linux, the Python executable is usually `venv/bin/python`.

## Security Model

This server is read-only by design:

1. Database credentials should use a `db_datareader`-only SQL Server login.
2. Built-in tools use hardcoded SELECT queries.
3. `query_schedule` accepts only a single read-only `SELECT` or `WITH ... SELECT`
   statement.
4. `query_schedule` rejects semicolons and unsafe keywords including `INSERT`,
   `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `EXEC`, `MERGE`, `GRANT`,
   `REVOKE`, `DBCC`, `BACKUP`, `RESTORE`, and `XP_CMDSHELL`.
5. Query results are capped to keep responses bounded.

Important: read-only does not mean non-sensitive. Any connected MCP client can
read data available to the configured SQL login. Run this server only with
trusted local clients and least-privilege credentials.

## P6 Database Notes

- P6 stores durations in hours. Most tools convert durations to workdays using
  activity, project, or fallback calendar hours per day.
- Critical path follows the project's P6 setting. `CT_DrivPath` uses
  `TASK.driving_path_flag`; `CT_TotFloat` uses
  `PROJECT.critical_drtn_hr_cnt`.
- Critical/path/analysis tools treat `TT_Task`, `TT_Rsrc`, `TT_Mile`, and
  `TT_FinMile` as schedule activities. `TT_LOE` and `TT_WBS` are excluded unless
  a tool explicitly states otherwise.
- Multiple float path tools report P6-calculated `TASK.float_path` and
  `TASK.float_path_order`. They do not synthesize multiple float paths if P6 has
  not calculated them.
- Longest path tools prefer P6-calculated fields where present, then use
  documented fallbacks.
- Driving predecessor/path tools infer relationship tightness from predecessor
  endpoint dates, successor endpoint dates, relationship type, lag, and parsed
  work calendars. P6 does not expose a relationship-level driving flag in
  `TASKPRED`.
- Activity code tools support both global (`AS_Global`) and project-scoped
  (`AS_Project`) code types where those codes are assigned to project
  activities.
- Baseline tools use P6 project baseline fields where available, but P6 baseline
  links can be null or stale. Outputs include resolution source and match
  confidence where relevant.
- Baseline/update comparisons match activities by `TASK.task_code`. P6 task IDs
  and WBS IDs are version-local and are not treated as cross-version identity
  keys.
- Relationship diagnostics use `TASKPRED.pred_type` and `lag_hr_cnt`, converting
  lag to days with activity calendars where possible.
- Dates are returned as `YYYY-MM-DD` where practical.

## Limitations

- This is an analysis interface, not a replacement for professional scheduling
  judgment.
- Schema details can vary between P6 versions and deployments.
- Some schedules may not have P6-calculated critical path, float path, early/late
  date, resource, or cost fields populated.
- Calendar parsing is best effort and may report caveats when base calendars or
  raw calendar data are incomplete.
- Related-update discovery uses transparent heuristics unless a hard P6
  baseline link is present.

## Project Structure

```text
p6-mcp/
|-- server.py                  # FastMCP server and tool registration
|-- db.py                      # SQL Server connection and query helpers
|-- tools/
|   |-- projects.py            # Project list
|   |-- eps.py                 # EPS tree and project discovery
|   |-- activities.py          # Activity lists, critical path, slippage, procurement
|   |-- activity_detail.py     # Full activity detail
|   |-- activity_metadata.py   # Activity codes, UDFs, activity search
|   |-- logic.py               # Open ends and project driving path
|   |-- paths.py               # Longest path, float paths, milestone/driving traces
|   |-- wbs.py                 # WBS hierarchy
|   |-- analysis.py            # Summary, schedule analysis, comparisons, WBS progress
|   |-- changes.py             # Detailed update comparison
|   |-- change_attribution.py  # Variance attribution
|   |-- baselines.py           # Baselines, related updates, timeline, WBS variance
|   |-- progress.py            # Progress diagnostics
|   |-- diagnostics.py         # Constraint and relationship diagnostics
|   |-- resources.py           # Resources, costs, cash flow
|   |-- calendars.py           # Calendar usage and detail
|   `-- query.py               # Capped read-only SQL tool
|-- skills/
|   `-- p6-mcp/                # Optional Codex skill for using these tools
|-- requirements.txt
|-- .env.example
|-- LICENSE
`-- README.md
```

## Contributing

Issues and pull requests are welcome. Before opening a pull request, please read
[CONTRIBUTING.md](CONTRIBUTING.md).

If you believe you have found a security issue, please read
[SECURITY.md](SECURITY.md).

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

Copyright (c) 2026 Atlas Works, LLC.

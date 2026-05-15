# Security Policy

## Supported Versions

Security fixes are handled on the default branch unless release branches are
created in the future.

## Reporting a Vulnerability

Please report security concerns privately before opening a public issue.

Contact Atlas Works, LLC through:

- Website: https://www.theatlasworks.com

Include:

- A description of the issue.
- The affected tool or code path.
- Whether database writes, credential disclosure, or unintended data exposure
  may be possible.
- Steps to reproduce, using sanitized data only.

## Security Model

P6 MCP Server is designed to be read-only:

- The recommended SQL Server login has `db_datareader` only.
- Built-in tools use SELECT queries.
- The freeform SQL tool accepts only a single read-only SELECT or WITH...SELECT
  statement and rejects write, DDL, privilege, execution, and administrative
  keywords.

Use a least-privilege database user. Do not run this server with P6 owner,
administrator, or write-capable SQL credentials.

Read-only access can still expose sensitive project data. Run this server only
with trusted MCP clients and only against data you are allowed to access.

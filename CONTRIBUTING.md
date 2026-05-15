# Contributing

Thanks for your interest in improving P6 MCP Server.

This project is intended to stay small, local-first, and read-only. Contributions
that preserve those boundaries are welcome.

## Ground Rules

- Keep database access read-only.
- Do not add tools that create, update, delete, recalculate, import, export, or
  otherwise mutate P6 database records.
- Prefer transparent schedule logic over black-box scoring.
- Preserve P6-native fields and labels where practical.
- Keep outputs structured and bounded so MCP clients can use them reliably.
- Do not commit private database credentials, client config, schedule exports,
  screenshots, or customer data.

## Development Setup

```bash
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

Copy `.env.example` to `.env` and configure a read-only SQL Server login.

## Validation

At minimum, run:

```bash
python -m compileall -q .
python -c "import server; print('server import ok')"
```

For database-facing changes, test against a non-production P6 database using a
read-only login.

## Pull Requests

Please include:

- What changed.
- Which P6 tables or fields are involved.
- Whether the change is read-only.
- How you tested it.
- Any known schema-version assumptions.

## License

By contributing, you agree that your contribution will be licensed under this
project's MIT License.

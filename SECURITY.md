# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Report privately:

- Open a [GitHub security advisory](https://github.com/ZYT64/KuroTutor/security/advisories/new), or
- Contact the maintainer via the repository owner profile.

You will get a response within a few days. Please include a description, reproduction steps, and affected version.

## Deployment security notes

- **API keys** live in `kuro.json` (gitignored). Never commit or share it. The template is `kuro.example.json`.
- The agent runs in a **sandboxed auto mode**: file operations are restricted to the workspace (path-traversal and symlink-escape protected), system settings are off-limits, shell is denied by default, and model endpoints are whitelisted.
- Student data stays on your own server (SQLite + files under `data/`). The project intentionally has no telemetry.
- Deploy on a trusted network. If exposing to the internet, keep `kuro serve --channel qq` behind the official QQ gateway and do not expose the FastAPI health port publicly.
- Run `kuro backup` regularly — it packages all student data into a single archive.

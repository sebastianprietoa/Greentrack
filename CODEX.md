# CODEX.md

This repo should be edited in small, safe steps.

## How to work with Codex

- Read `AGENTS.md` first.
- Stay inside the requested scope.
- Do not move routes without explicit instruction.
- Do not change templates or static files unless asked.
- Do not change endpoint names unless asked.
- Keep `DATABASE_URL` coming from environment variables.
- Keep `app:app` working for Gunicorn.
- Check for import cycles when new modules are added.
- Validate with `python -m compileall .`.

## Preferred approach

- Make incremental changes.
- Prefer files under `routes/`, `services/`, `utils/`, `db.py`, and `auth_utils.py` for future refactors.
- Keep documentation and scripts aligned with the codebase.

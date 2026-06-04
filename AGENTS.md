# AGENTS.md

GreenTrack is a Flask app deployed on Railway with Gunicorn using `web: gunicorn app:app`.

## Rules for agents

- Do not change routes unless a task explicitly asks for it.
- Do not hardcode credentials, tokens, passwords, or database URLs.
- Do not modify templates or static files unless requested.
- Keep Railway compatibility.
- Keep `app:app` available for Gunicorn.
- Keep `DATABASE_URL` read from the environment.
- Make small, incremental changes.
- Validate with `python -m compileall .` before finishing.

## Working style

- Inspect the current files first.
- Reuse existing patterns.
- Avoid refactors outside the requested scope.
- Document any important assumption briefly.

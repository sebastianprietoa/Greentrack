# Architecture

GreenTrack is a modular monolith built on Flask.

## Current direction

- `app.py`: application entrypoint and runtime bootstrap.
- `config.py`: environment-based configuration placeholder.
- `db.py`: database connection helper based on `DATABASE_URL`.
- `auth_utils.py`: password helpers.
- `routes/`: Flask Blueprints for grouped HTTP routes.
- `services/`: business logic and reusable calculations.
- `repositories/`: future SQL and data access layer.
- `utils/`: small shared utilities.
- `scripts/`: operational scripts for backups and database cloning.
- `templates/` and `static/`: kept intact.

## Principles

- Keep route handlers thin.
- Move shared business logic into services.
- Keep SQL and data access isolated when the repository layer is added.
- Avoid visual or template changes during architecture preparation.
- Keep Railway deployment behavior stable.

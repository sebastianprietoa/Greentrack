# Railway Deploy

## Required variables

- `DATABASE_URL`
- `SECRET_KEY`

## Current process

- Railway runs the app with `web: gunicorn app:app`.
- Do not hardcode credentials in the repository.
- Keep database values in Railway service variables.
- Keep `DATABASE_URL` read from the environment.
- Redeploy the web service after changing variables.

## Notes

- Do not modify `Procfile` in this stage.
- Do not change templates, static files, or routes for deployment preparation.

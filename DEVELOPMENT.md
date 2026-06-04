# Development

## Local setup

1. Create a virtual environment with `python -m venv .venv`.
2. Activate it.
   - Windows PowerShell: `.venv\Scripts\Activate.ps1`
   - macOS or Linux: `source .venv/bin/activate`
3. Install dependencies with `pip install -r requirements.txt`.
4. Load your local environment file if you use one, such as `.env.local`.
5. Run `python app.py`.
6. Or run `gunicorn app:app --bind 0.0.0.0:5000`.
7. Open `http://localhost:5000`.

## Helpful commands

- Validate syntax with `python -m compileall .`
- Use the same Gunicorn command that Railway uses in production.

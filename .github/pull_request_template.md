## Summary

## Validation

- [ ] `python -m compileall .`
- [ ] `python app.py` tested locally, if applicable
- [ ] `gunicorn app:app --bind 0.0.0.0:5000` tested, if applicable

## Checklist

- [ ] I did not change existing routes
- [ ] I did not add credentials, tokens, or secrets
- [ ] I did not touch templates or static files unnecessarily
- [ ] I kept Railway compatibility
- [ ] I kept `app:app` for Gunicorn

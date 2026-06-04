# Roadmap

## Fase 1: Archivos base y documentacion

- Create architecture files, scripts, and CI scaffolding.
- Keep the application behavior unchanged.

## Fase 2: Modularizacion con Blueprints

- Move grouped routes into `routes/` incrementally.
- Keep existing URLs and endpoint names.

## Fase 3: `huella_service.py`

- Move reusable emissions logic into services.
- Keep the calculation results unchanged.

## Fase 4: Endpoints JSON

- Extract shared JSON helpers and API responses.
- Keep the API contract stable.

## Fase 5: Seguridad

- Review sessions, admin handling, and input validation.
- Add security improvements without breaking behavior.

## Fase 6: Tests y migraciones

- Add automated tests and migration support.
- Verify compatibility with Railway and local execution.

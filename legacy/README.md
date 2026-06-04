# Legacy cleanup log

Fecha de limpieza: 2026-06-04

## Criterio usado

- Se conservaron activos los archivos referenciados por `render_template`, `{% extends %}`, `{% include %}` y los assets activos en `static/`.
- Se movieron a `legacy/` las copias sueltas, duplicadas, antiguas o no referenciadas.
- No se borro ningun archivo dudoso; todo se guardo como respaldo.
- Los archivos generados o sensibles quedaron fuera de Git mediante `.gitignore`.

## Archivos movidos

- `legacy/duplicates/root_html/`: `admin.html`, `admin_detalle.html`, `agua_dashboard.html`, `agua_registro.html`, `agua_reporte.html`, `base.html`, `combustible_fijo.html`, `combustible_movil.html`, `combustion_dashboard.html`, `configuracion.html`, `dashboard.html`, `electricidad_dashboard.html`, `formulario_residuos.html`, `importar.html`, `index.html`, `login.html`, `mis_datos.html`, `refrigerantes_dashboard.html`, `registro.html`, `residuos.html`, `residuos_reporte.html`, `vehiculos.html`.
- `legacy/duplicates/templates_nested/`: `templates/admin.html`, `templates/admin_detalle.html`, `templates/agua_dashboard.html`, `templates/agua_registro.html`, `templates/agua_reporte.html`, `templates/base.html`, `templates/combustible_fijo.html`, `templates/combustible_movil.html`, `templates/combustion_dashboard.html`, `templates/configuracion.html`, `templates/dashboard.html`, `templates/electricidad_dashboard.html`, `templates/formulario_residuos.html`, `templates/importar.html`, `templates/index.html`, `templates/login.html`, `templates/mis_datos.html`, `templates/refrigerantes_dashboard.html`, `templates/registro.html`, `templates/residuos.html`, `templates/residuos_reporte.html`, `templates/vehiculos.html`.
- `legacy/assets_root/`: `app.js`, `style.css`, `logo_left.png`.
- `legacy/misc_root/`: `codigo`, `postman_emisiones_por_empresa.postman_collection.json`.
- `legacy/unused_candidates/`: `templates_app.py`.

## Conflictos detectados

- Las copias sueltas de HTML en la raiz diferian de las versiones activas dentro de `templates/`.
- `style.css` en la raiz diferia de `static/style.css`, por eso se dejo como respaldo legacy.
- `app.js` y `logo_left.png` en la raiz eran duplicados de versiones ya presentes en `static/`.
- La carpeta `templates/templates/` contenia otra copia completa de templates activos, por eso se movio como duplicado.

## Revision manual sugerida

- `legacy/unused_candidates/templates_app.py`
- `legacy/misc_root/codigo`
- `legacy/misc_root/postman_emisiones_por_empresa.postman_collection.json`

## Archivos generados o sensibles fuera de Git

- `.env`
- `greentrack_backup.dump`
- `__pycache__/`
- `*.pyc`
- `*.dump`
- `*.sql`
- `*.backup`
- `logs/`

## Notas

- No se modifico la logica de negocio ni las rutas Flask.
- Solo se movieron copias y archivos no usados a `legacy/` para dejar la raiz mas limpia.

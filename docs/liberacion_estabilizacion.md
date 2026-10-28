# Liberación de la estabilización

Estado: candidato técnico; requiere aceptación funcional antes de producción.
Conservar el estilo existente, `app:app`, Procfile y DATABASE_URL desde el entorno.
El ZIP y su manifiesto identifican los bytes exactos del candidato aunque la rama
contenga cambios sin commit. El diff de revisión omite diferencias de fin de línea;
los archivos nuevos completos están en el ZIP y el manifiesto.

## Validación previa

1. Revisar el manifiesto, el diff y los archivos nuevos. No incluir `.env`, dumps,
   logs, cuentas de prueba ni documentos de empresas en Git o en el despliegue.
2. Ejecutar `python -m pip install -r requirements-dev.txt` en un entorno aislado.
3. Proporcionar TEST_DATABASE_URL del clon local `greentrack_staging` y
   GREENTRACK_INIT_DB=0; ejecutar `python -m pytest -q` y
   `python -m compileall .`. Nunca ejecutar pruebas contra producción.
4. Aceptación humana: probar registro, modificación y eliminación de consumos,
   diagnóstico de agua y modificación, dashboards, exportación y permisos por rol.
   Ver las evidencias de etapas 5 y 6. La validación automática no reemplaza esta aceptación.

## Respaldo y migración controlada

Preparar una ventana sin escrituras y registrar el despliegue anterior exitoso y
sus variables. Configurar DATABASE_URL y PGSSLMODE mediante el entorno del operador,
sin escribir credenciales en comandos o documentos. Desactivar inicialización con
GREENTRACK_INIT_DB=0. Utilizar pg_dump/pg_restore de la misma versión mayor del servidor.

Ejecutar por separado:

```text
python scripts/release_database.py
python scripts/release_database.py --backup DIRECTORIO_PRIVADO_NUEVO --bin-dir RUTA_BIN_POSTGRES
python scripts/release_database.py --apply --target-id IDENTIFICADOR_DEL_PREFLIGHT --backup-manifest DIRECTORIO_PRIVADO_NUEVO/backup.json
```

La consulta inicial es de solo lectura. El respaldo usa una instantánea compartida
para que los conteos correspondan al dump, verifica su listado y registra SHA256.
Antes de aplicar se exige respaldo íntegro de menos de 24 horas del mismo destino.
Para un destino remoto, habilitar además GREENTRACK_ALLOW_RELEASE_MIGRATION=1 y SSL.
La migración archiva suministros huérfanos en una tabla de cuarentena, elimina solo
esos derivados, desvincula referencias móviles inválidas y valida las dos claves
foráneas con propagación de renombrados y eliminaciones. Se confirma en una transacción.
No recalcula factores ni emisiones históricas. Los errores hacen rollback.

Respaldar en almacenamiento privado persistente y ensayar su restauración antes
de producción. Railway ejecuta pre-deploy en un contenedor separado y sus archivos
locales no persisten: no usarlo como almacenamiento de respaldo.
[Railway pre-deploy](https://docs.railway.com/deployments/pre-deploy-command),
[PostgreSQL pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html).

## Publicación y comprobación

Tras aceptación, publicar exclusivamente el candidato revisado en el servicio
Railway correcto. Mantener `web: gunicorn app:app`; configurar una clave de sesión
privada y GREENTRACK_INIT_DB=0. Confirmar login y lecturas sin errores, permiso por
rol, coherencia entre sumas y dashboards y exportaciones. Las pruebas de escritura
deben hacerse con una empresa de prueba autorizada, no con empresas reales.
No declarar Linux/Gunicorn verificados solo por una ejecución en Windows: completar
CI en Ubuntu antes de liberar. La ejecución remota de CI requiere publicar la rama.

En instalaciones nuevas, la creación opcional del administrador requiere
ADMIN_INITIAL_PASSWORD en el entorno de inicialización. La inicialización conserva
las cuentas existentes; retirar esa variable después de crear la cuenta.

## Recuperación

Ante errores, detener escrituras. Volver al despliegue anterior desde Railway
recupera imagen y variables; no restaura la base de datos.
[Acciones de despliegue](https://docs.railway.com/deployments/deployment-actions).
Las nuevas restricciones son compatibles con datos íntegros; evaluar primero un
rollback de código. Si hace falta recuperar datos, restaurar el dump en una base
nueva, verificar los conteos del manifiesto y las funcionalidades, y solo entonces
cambiar la conexión mediante un procedimiento autorizado. No sobrescribir la base
activa: se perderían las escrituras posteriores al respaldo.
Conservar base original, cuarentena, manifiesto y dump para reconciliar esas escrituras.


## Cierre del plan de correcciones (27 de octubre de 2026)

Las etapas 1 a 4 corrigieron medidas anuales, períodos y exportaciones, unidades y redondeos, y validaciones/mensajes. La suite incorpora 254 casos. La etapa 5 prepara un candidato con los bytes finales y su manifiesto; la evidencia funcional y las limitaciones se conservan en outputs/estabilizacion/usuario_2025. No se incluyen esas evidencias ni los archivos de empresas en el ZIP.

Para revisar la aceptación final, consultar CORRECCION_5_ACEPTACION_CANDIDATO.md en esa carpeta. La importación real por interfaz de una fila sintética y la eliminación del registro descartable 163 se conciliaron con los IDs previos y con su derivado de suministro. Se conservan los 84 registros anuales de prueba. La cancelación automatizada del diálogo nativo no se declara verificada; sí hubo una cancelación humana en la auditoría inicial.

No publicar candidatos anteriores a este cierre. Verificar en el manifiesto que estén incluidos filtro_anio_panel.html y las pruebas de configuración anual, períodos, unidades y validaciones. Antes de producción, revisar los resultados del candidato aislado, el alcance real de la comprobación Linux/Gunicorn y la aceptación del responsable; no confundir un arranque WSGI con una suite completa en Linux.

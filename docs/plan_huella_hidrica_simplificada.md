# Plan técnico: Water Footprint

## Objetivo

Diseñar e implementar un nuevo módulo de **Water Footprint** para GreenTrack, basado en una estimación operacional simplificada de flujos directos de agua.

El módulo debe registrar y mostrar, por sede o empresa:

1. **Captación o entrada de agua** `[m³]`
2. **Retorno o descarga de agua** `[m³]`
3. **Consumo operativo estimado** `[m³]`
4. **Reúso interno** `[m³]`
5. **Huella de escasez hídrica** `[m³-eq]`, solo cuando exista un factor de escasez configurado para la ubicación

Regla conceptual base:

- **Consumo operativo estimado** = entradas externas de agua − retornos verificables al mismo sistema hídrico
- **Huella de escasez hídrica** = consumo operativo `[m³]` × factor local de escasez `[m³-eq/m³]`

Condición obligatoria:

- No inventar factores de escasez.
- Si una sede no tiene factor configurado, el sistema debe indicar que la huella de escasez no está disponible.
- Aun sin factor, el sistema debe entregar siempre los indicadores físicos en `[m³]`.

## 1. Archivos y rutas existentes relacionadas con agua

### Backend

- [`app.py`](../app.py)
  - Creación de tablas actuales:
    - `agua_consumo`
    - `agua_afluentes`
    - `agua_cuencas`
    - `agua_costos`
  - Ruta principal de agua:
    - `GET /agua`
  - Rutas auxiliares:
    - `GET/POST /agua/registro`
    - `GET /agua/reporte`

### Templates

- [`templates/base.html`](../templates/base.html)
  - Incluye el acceso al módulo “Agua” en el menú lateral.
- [`templates/agua_dashboard.html`](../templates/agua_dashboard.html)
  - Vista actual del módulo de agua.
- [`templates/agua_registro.html`](../templates/agua_registro.html)
  - Formulario actual de registro.
- [`templates/agua_reporte.html`](../templates/agua_reporte.html)
  - Reporte actual del módulo.

### Contexto adicional

- [`ARCHITECTURE.md`](../ARCHITECTURE.md)
  - Describe intención de modularizar gradualmente.
- [`ROADMAP.md`](../ROADMAP.md)
  - Menciona una futura `huella_service.py`.
- [`db.py`](../db.py)
  - Helper de conexión a PostgreSQL con `DATABASE_URL`.

## 2. Modelo de datos actual y brechas frente al nuevo módulo

### Modelo actual detectado

El módulo histórico de agua está orientado a calidad de efluentes y costos asociados. Según `app.py`, hoy existen estas tablas:

- `agua_consumo`
  - `empresa`
  - `fecha`
  - `agua_embotellada_litros`
  - `hielo_comprado_kg`
  - `hielo_producido_kg`
  - `tiene_tratamiento`
  - `descripcion_tratamiento`
- `agua_afluentes`
  - `empresa`
  - `fecha`
  - `tipo`
  - `caudal_m3`
  - `tratamiento`
- `agua_cuencas`
  - `empresa`
  - `fecha`
  - `tipo_cuenca`
  - `cantidad_m3`
- `agua_costos`
  - `empresa`
  - `fecha`
  - `concepto`
  - `cantidad`
  - `unidad`
  - `costo_usd`
  - `costo_clp`

### Brechas respecto al nuevo objetivo

1. **No existe un concepto explícito de captación/entrada**
   - Hoy hay afluentes y cuencas, pero no un registro claro de entrada externa por sede con trazabilidad operativa.

2. **No existe un concepto explícito de retorno verificable**
   - El modelo actual usa “cuencas de destino” y “afluentes/efluentes”, pero no separa retorno al mismo sistema hídrico versus descarga a otro destino.

3. **No existe un cálculo formal de consumo operativo**
   - La lógica actual mezcla consumo de agua embotellada, hielo y un cálculo agregado que no representa la nueva definición.

4. **No existe reúso interno como indicador propio**
   - Falta campo o tabla para registrar agua reutilizada dentro de la operación.

5. **No existe factor de escasez configurado por ubicación**
   - No hay tabla ni relación con sede, ubicación o contexto geográfico.

6. **No existe resultado en `m³-eq` condicionado por disponibilidad**
   - El sistema actual no distingue entre métricas físicas y métricas ponderadas por escasez.

7. **El módulo actual está acoplado a agua embotellada/hielo**
   - Es útil como histórico, pero no suficiente como base semántica del nuevo módulo.

## 3. Nueva arquitectura recomendada

### Principio general

Mantener el módulo actual como base histórica y de compatibilidad, pero introducir un nuevo submódulo funcional con semántica clara para huella hídrica simplificada.

### Arquitectura propuesta

1. **Capa de presentación**
   - Nuevos templates específicos para el módulo de huella hídrica simplificada.
   - Mantener los templates actuales de agua como submódulo histórico o de compatibilidad.

2. **Capa de rutas**
   - Idealmente extraer el bloque de agua a un blueprint nuevo o futuro.
   - Por restricción de compatibilidad, primero se puede mantener en `app.py` y luego mover gradualmente.

3. **Capa de servicio**
   - Crear lógica de cálculo en un servicio dedicado, por ejemplo:
     - `services/huella_hidrica_service.py`
   - Aquí se calcula:
     - entradas
     - retornos
     - consumo operativo
     - reúso interno
     - huella de escasez, si existe factor

4. **Capa de persistencia**
   - Agregar tablas específicas para entradas, retornos, reúso y factores de escasez.
   - Mantener tablas antiguas para histórico.

5. **Capa de compatibilidad**
   - Mapear datos históricos del módulo actual a un esquema de lectura compatible, sin reescribir datos viejos de inmediato.

## 4. Tablas nuevas o cambios de esquema requeridos

### Opción recomendada: tablas nuevas, sin romper las actuales

#### Tabla `huella_hidrica_sedes`

Catálogo de sedes o unidades operativas para asociar factores y registros.

- `id`
- `empresa`
- `nombre_sede`
- `ubicacion`
- `region`
- `pais`
- `activa`

#### Tabla `huella_hidrica_registros`

Registro base por periodo o evento.

- `id`
- `empresa`
- `sede_id`
- `fecha`
- `periodo`
- `captacion_m3`
- `retorno_m3`
- `reuso_interno_m3`
- `consumo_operativo_m3`
- `factor_escasez`
- `huella_escasez_meq`
- `factor_disponible` `BOOLEAN`
- `observaciones`
- `fuente_datos`

#### Tabla `huella_hidrica_movimientos`

Para detalle trazable de entradas y salidas.

- `id`
- `empresa`
- `sede_id`
- `registro_id`
- `tipo_movimiento` `("entrada", "retorno", "reuso")`
- `descripcion`
- `cantidad_m3`
- `sistema_hidrico_origen`
- `sistema_hidrico_destino`
- `verificable` `BOOLEAN`
- `documento_soporte`

#### Tabla `huella_hidrica_factores_escasez`

Factor local por ubicación o sede.

- `id`
- `ubicacion`
- `region`
- `pais`
- `factor_escasez_meq_por_m3`
- `fuente`
- `vigencia_desde`
- `vigencia_hasta`
- `activo`

### Cambios opcionales sobre tablas existentes

Si se busca compatibilidad histórica más directa, se pueden agregar columnas mínimas a `agua_consumo` o crear una vista de transición, pero la recomendación principal es **no sobrecargar** las tablas actuales.

## 5. Rutas Flask necesarias

### Nuevas rutas sugeridas

- `GET /huella-hidrica`
  - Dashboard principal del nuevo módulo.
- `GET/POST /huella-hidrica/registro`
  - Registro de entradas, retornos y reúso.
- `GET /huella-hidrica/reporte`
  - Reporte con totales físicos y estado de factor de escasez.
- `GET /huella-hidrica/sedes`
  - Administración de sedes.
- `GET/POST /huella-hidrica/factores`
  - Configuración de factores de escasez por ubicación.
- `GET /huella-hidrica/historico`
  - Lectura compatible de datos antiguos del módulo de agua.

### Compatibilidad deseada

- Mantener `GET /agua` y las rutas actuales como módulo histórico o compatibilidad.
- No cambiar rutas existentes salvo que el cambio sea estrictamente necesario.

## 6. Templates nuevos o modificados

### Nuevos templates recomendados

- `templates/huella_hidrica_dashboard.html`
- `templates/huella_hidrica_registro.html`
- `templates/huella_hidrica_reporte.html`
- `templates/huella_hidrica_sedes.html`
- `templates/huella_hidrica_factores.html`
- `templates/huella_hidrica_historico.html`

### Templates existentes a conservar

- `templates/agua_dashboard.html`
- `templates/agua_registro.html`
- `templates/agua_reporte.html`

### Ajuste mínimo sugerido

- Agregar una entrada de menú para el nuevo módulo.
- Mantener el módulo de agua actual como acceso a histórico o transición.

## 7. Estrategia de migración de datos

### Enfoque recomendado

Migración **no destructiva** y por etapas.

1. **Paso 1: solo lectura histórica**
   - Dejar tablas actuales intactas.
   - Crear vistas o adaptadores de lectura desde el nuevo módulo.

2. **Paso 2: mapeo histórico parcial**
   - Mapear registros de `agua_afluentes` y `agua_cuencas` a equivalentes conceptuales:
     - afluentes -> entradas o retornos según contexto semántico
     - cuencas -> retornos/descargas
   - Esta asignación debe revisarse manualmente si los datos antiguos no son inequívocos.

3. **Paso 3: coexistencia**
   - Nuevos registros se guardan en el esquema nuevo.
   - Los históricos siguen disponibles en consultas de compatibilidad.

4. **Paso 4: consolidación**
   - Si se valida la transición, los reportes usan el esquema nuevo como fuente principal.

### Recomendación crítica

No hacer una migración automática completa de los datos actuales a métricas nuevas sin una revisión semántica, porque el modelo viejo mezcla agua, hielo, costos y tratamiento.

## 8. Riesgos de compatibilidad

1. **Ambigüedad semántica de datos históricos**
   - Los registros actuales no distinguen claramente entradas, retornos y reúso.

2. **Riesgo de romper reportes existentes**
   - Si se reemplaza la lógica actual de golpe, pueden fallar vistas o totales históricos.

3. **Riesgo de doble conteo**
   - Si se combinan tablas nuevas y antiguas sin reglas de precedencia, el consumo podría contarse dos veces.

4. **Riesgo de mostrar `m³-eq` sin factor válido**
   - Debe evitarse calcular escasez con factores por defecto no trazables.

5. **Riesgo de cambios en templates existentes**
   - Se recomienda no modificar los templates actuales salvo para enlaces o mensajes mínimos de transición.

## 9. Plan de implementación por etapas

### Etapa 0: Preparación

- Congelar el comportamiento actual del módulo de agua.
- Documentar mapeo conceptual entre modelo viejo y nuevo.

### Etapa 1: Base de datos

- Crear tablas nuevas para sedes, registros, movimientos y factores.
- Agregar índices y claves foráneas mínimas.

### Etapa 2: Servicio de cálculo

- Crear `services/huella_hidrica_service.py`.
- Implementar:
  - cálculo de consumo operativo
  - cálculo de huella de escasez condicionada a factor disponible
  - mensajes de “no disponible” cuando no exista factor

### Etapa 3: Rutas y templates nuevos

- Crear endpoints nuevos.
- Crear dashboard, formulario y reporte del nuevo módulo.

### Etapa 4: Compatibilidad histórica

- Exponer vistas de histórico.
- Reutilizar registros antiguos donde sea posible sin reinterpretarlos de forma riesgosa.

### Etapa 5: Integración gradual

- Agregar enlaces desde el menú.
- Mantener el módulo viejo visible o etiquetado como histórico.

### Etapa 6: Depuración futura

- Evaluar si el módulo antiguo de agua se convierte en submódulo histórico, se reetiqueta o se retira en una fase posterior.

## 10. Casos de prueba funcionales y de cálculo

### Casos base

1. **Con factor de escasez configurado**
   - Entrada: `100 m³`
   - Retorno verificable: `30 m³`
   - Reúso interno: `10 m³`
   - Consumo operativo esperado: `70 m³`
   - Si factor local: `0.5 m³-eq/m³`
   - Huella esperada: `35 m³-eq`

2. **Sin factor de escasez**
   - Entrada: `80 m³`
   - Retorno: `20 m³`
   - Consumo operativo: `60 m³`
   - Resultado esperado:
     - mostrar `60 m³`
     - mostrar `huella de escasez no disponible`

3. **Con retornos mayores que entradas**
   - Entrada: `50 m³`
   - Retorno verificable: `60 m³`
   - Regla esperada:
     - consumo operativo no debería ser negativo
     - el sistema debe normalizar a `0 m³` o bloquear el registro con validación clara

4. **Con reúso interno registrado**
   - Entrada: `120 m³`
   - Retorno: `40 m³`
   - Reúso: `15 m³`
   - Consumo operativo esperado:
     - debe mostrarse como métrica separada
     - el reúso no debe confundirse con retorno al mismo sistema hídrico

5. **Factor vigente por ubicación**
   - Una sede con factor activo y otra sin factor.
   - El sistema debe calcular huella solo donde el factor exista y dejar el resto como no disponible.

### Validaciones de negocio

- No permitir calcular huella de escasez con factor vacío, nulo o inventado.
- No perder los indicadores físicos cuando la huella ponderada no esté disponible.
- Evitar que “reúso interno” se sume por error como retorno verificable si no corresponde.

## 11. Decisión sobre componentes antiguos

### Se mantienen

- `agua_consumo`
- `agua_afluentes`
- `agua_cuencas`
- `agua_costos`
- `templates/agua_dashboard.html`
- `templates/agua_registro.html`
- `templates/agua_reporte.html`
- Ruta `GET /agua` durante la transición

### Se reemplazan gradualmente

- Cálculo actual de agua en `agua_dashboard()` para la lógica nueva de huella hídrica simplificada.
- Reportes basados en agua embotellada/hielo como métrica principal.

### Quedan como submódulos futuros o históricos

- Tratamiento de agua detallado tipo RILES, si se decide separarlo más adelante.
- Costos asociados de agua, si se desea mantenerlos como módulo financiero complementario.
- Históricos antiguos del módulo de agua, mientras se valida la migración semántica.

## 12. Recomendación final

La transición más segura es consolidar el módulo principal como **Water Footprint**, mantener el módulo histórico de agua como compatibilidad, y mover la lógica de cálculo a un servicio dedicado antes de reemplazar cualquier vista o ruta existente.

Esto reduce riesgo, protege datos históricos y permite evolucionar el producto sin romper los flujos actuales.

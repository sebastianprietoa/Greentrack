# Metodología Water Footprint

## Propósito

Este módulo estima la huella hídrica de operaciones directas de una empresa con foco en:

- captación de agua
- retornos verificables
- reúso interno
- consumo operativo estimado
- huella de escasez cuando existe un factor local válido

## Qué mide

- **Captación [m³]**: agua que entra a la operación.
- **Retorno [m³]**: agua que vuelve a un sistema hídrico o destino declarado.
- **Reúso [m³]**: agua utilizada nuevamente dentro de la propia operación.
- **Consumo operativo estimado [m³]**: captación menos retornos verificables al mismo sistema hídrico.
- **Huella de escasez [m³-eq]**: consumo operativo multiplicado por un factor local válido.

## Qué no mide

- cadena de valor
- agua indirecta
- evaluación de calidad de agua
- impactos de ciclo de vida completo

## Regla de cálculo

La huella de escasez solo se calcula si existe un factor configurado con:

- método
- versión
- fuente
- ubicación
- vigencia

Si no existe factor válido, el sistema muestra solo los indicadores físicos en m³.

## Mensaje obligatorio

Este reporte presenta una estimación operacional simplificada basada en metodología Water Footprint para operaciones directas. No constituye una verificación externa ni una evaluación completa de ciclo de vida.

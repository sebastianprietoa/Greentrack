"""Logica de huella hidrica simplificada para GreenTrack."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional


SERVICE_VERSION = "1.1.0"


class HuellaAguaError(ValueError):
    """Error de validacion o consistencia para el modulo de huella hidrica."""


def _to_decimal(value: Any, field_name: str) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise HuellaAguaError(f"{field_name} debe ser numerico.") from exc


def _ensure_non_negative(value: Decimal, field_name: str) -> Decimal:
    if value < 0:
        raise HuellaAguaError(f"{field_name} no puede ser negativo.")
    return value


def _normalizar_fecha_periodo(valor: Any) -> Optional[date]:
    if valor in (None, ""):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor

    texto = str(valor).strip()
    if not texto:
        return None

    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(texto, fmt)
        except ValueError:
            continue
        if fmt == "%Y":
            return parsed.date().replace(month=1, day=1)
        if fmt == "%Y-%m":
            return parsed.date().replace(day=1)
        return parsed.date()
    return None


def calcular_captacion_total(flujos: Iterable[dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for flujo in flujos:
        if flujo.get("tipo_flujo") == "captacion":
            total += _ensure_non_negative(_to_decimal(flujo.get("volumen_m3"), "volumen_m3"), "volumen_m3")
    return total


def calcular_retornos_totales(flujos: Iterable[dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for flujo in flujos:
        if flujo.get("tipo_flujo") == "retorno":
            total += _ensure_non_negative(_to_decimal(flujo.get("volumen_m3"), "volumen_m3"), "volumen_m3")
    return total


def calcular_reuso_interno(flujos: Iterable[dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for flujo in flujos:
        if flujo.get("tipo_flujo") == "reuso":
            total += _ensure_non_negative(_to_decimal(flujo.get("volumen_m3"), "volumen_m3"), "volumen_m3")
    return total


def calcular_retornos_mismo_sistema(flujos: Iterable[dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for flujo in flujos:
        if flujo.get("tipo_flujo") == "retorno" and flujo.get("retorna_mismo_sistema_hidrico"):
            total += _ensure_non_negative(_to_decimal(flujo.get("volumen_m3"), "volumen_m3"), "volumen_m3")
    return total


def calcular_consumo_operativo_estm(
    captacion_total: Any,
    retornos_mismo_sistema: Any,
    reuso_interno: Any = 0,
    justifico_reuso_superior: bool = False,
) -> Decimal:
    captacion = _ensure_non_negative(_to_decimal(captacion_total, "captacion_total"), "captacion_total")
    retornos = _ensure_non_negative(_to_decimal(retornos_mismo_sistema, "retornos_mismo_sistema"), "retornos_mismo_sistema")
    reuso = _ensure_non_negative(_to_decimal(reuso_interno, "reuso_interno"), "reuso_interno")

    if retornos > captacion:
        raise HuellaAguaError("Los retornos al mismo sistema no pueden superar la captacion total.")
    if reuso > captacion and not justifico_reuso_superior:
        raise HuellaAguaError("El reuso interno no puede superar la captacion total sin justificacion explicita.")

    consumo = captacion - retornos
    return consumo if consumo >= 0 else Decimal("0")


def calcular_consumo_operativo_estimado(
    captacion_total: Any,
    retornos_mismo_sistema: Any,
    reuso_interno: Any = 0,
    justifico_reuso_superior: bool = False,
) -> Decimal:
    return calcular_consumo_operativo_estm(
        captacion_total=captacion_total,
        retornos_mismo_sistema=retornos_mismo_sistema,
        reuso_interno=reuso_interno,
        justifico_reuso_superior=justifico_reuso_superior,
    )


def calcular_intensidad_hidrica_total(huella_total_m3: Any, medida_productiva: Any) -> Optional[Decimal]:
    huella_total = _ensure_non_negative(_to_decimal(huella_total_m3, "huella_total_m3"), "huella_total_m3")
    if medida_productiva in (None, "", 0, "0"):
        return None
    try:
        medida = Decimal(str(medida_productiva))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if medida <= 0:
        return None
    return huella_total / medida


@dataclass(frozen=True)
class FactorEscasez:
    id: Any
    metodo: str
    version_metodo: str
    actividad: str
    nivel_geografico: str
    codigo_geografico: str
    periodo_inicio: Any
    periodo_fin: Any
    factor_m3eq_m3: Decimal
    fuente: str
    referencia: str
    fecha_carga: Any
    activo: bool = True


def validar_factor_escasez(factor: dict[str, Any] | FactorEscasez) -> FactorEscasez:
    data = factor.__dict__ if isinstance(factor, FactorEscasez) else factor
    metodo = (data.get("metodo") or "").strip()
    version_metodo = (data.get("version_metodo") or "").strip()
    fuente = (data.get("fuente") or "").strip()
    factor_value = _ensure_non_negative(_to_decimal(data.get("factor_m3eq_m3"), "factor_m3eq_m3"), "factor_m3eq_m3")

    if factor_value <= 0:
        raise HuellaAguaError("El factor de escasez debe ser mayor que cero.")
    if not metodo or not version_metodo or not fuente:
        raise HuellaAguaError("El factor de escasez debe incluir metodologia, version y fuente.")

    return FactorEscasez(
        id=data.get("id"),
        metodo=metodo,
        version_metodo=version_metodo,
        actividad=(data.get("actividad") or "").strip(),
        nivel_geografico=(data.get("nivel_geografico") or "").strip(),
        codigo_geografico=(data.get("codigo_geografico") or "").strip(),
        periodo_inicio=data.get("periodo_inicio"),
        periodo_fin=data.get("periodo_fin"),
        factor_m3eq_m3=factor_value,
        fuente=fuente,
        referencia=(data.get("referencia") or "").strip(),
        fecha_carga=data.get("fecha_carga"),
        activo=bool(data.get("activo", True)),
    )


def buscar_factor_escasez_mas_especifico(
    factores: Iterable[dict[str, Any] | FactorEscasez],
    sede: dict[str, Any],
    periodo: Any,
) -> Optional[FactorEscasez]:
    candidatos: list[FactorEscasez] = []
    codigo_cuenca = (sede.get("codigo_cuenca") or "").strip()
    region = (sede.get("region") or "").strip()
    pais = (sede.get("pais") or "Chile").strip()
    comuna = (sede.get("comuna") or "").strip()
    periodo_normalizado = _normalizar_fecha_periodo(periodo)

    for factor in factores:
        parsed = validar_factor_escasez(factor)
        if not parsed.activo:
            continue
        periodo_inicio = _normalizar_fecha_periodo(parsed.periodo_inicio)
        periodo_fin = _normalizar_fecha_periodo(parsed.periodo_fin)
        if periodo_normalizado and periodo_inicio and periodo_normalizado < periodo_inicio:
            continue
        if periodo_normalizado and periodo_fin and periodo_normalizado > periodo_fin:
            continue
        codigo = parsed.codigo_geografico.strip()
        if parsed.nivel_geografico == "cuenca" and codigo and codigo == codigo_cuenca:
            candidatos.append(parsed)
        elif parsed.nivel_geografico == "subnacional" and codigo and codigo in {region, comuna}:
            candidatos.append(parsed)
        elif parsed.nivel_geografico == "pais" and codigo and codigo == pais:
            candidatos.append(parsed)

    prioridad = {"cuenca": 0, "subnacional": 1, "pais": 2}
    if not candidatos:
        return None
    candidatos.sort(key=lambda f: prioridad.get(f.nivel_geografico, 99))
    return candidatos[0]


def seleccionar_factor_para_sede(
    factores: Iterable[dict[str, Any] | FactorEscasez],
    sede: dict[str, Any],
    periodo: Any,
) -> Optional[FactorEscasez]:
    return buscar_factor_escasez_mas_especifico(factores, sede, periodo)


def calcular_huella_escasez(consumo_operativo_m3: Any, factor: Optional[FactorEscasez]) -> Optional[Decimal]:
    if factor is None:
        return None
    consumo = _ensure_non_negative(_to_decimal(consumo_operativo_m3, "consumo_operativo_m3"), "consumo_operativo_m3")
    factor_validado = validar_factor_escasez(factor)
    return consumo * factor_validado.factor_m3eq_m3


def clasificar_resultado_huella(
    consumo_operativo_m3: Any,
    factor: Optional[FactorEscasez],
    datos_suficientes: bool = True,
) -> str:
    if not datos_suficientes:
        return "Datos insuficientes"
    if factor is None:
        return "Solo inventario físico disponible"
    consumo = _ensure_non_negative(_to_decimal(consumo_operativo_m3, "consumo_operativo_m3"), "consumo_operativo_m3")
    if consumo <= 0:
        return "Solo inventario físico disponible"
    return "Huella de escasez calculada"


def generar_indicador_calidad_datos(flujos: Iterable[dict[str, Any]]) -> str:
    puntaje = 100
    for flujo in flujos:
        calidad = (flujo.get("calidad_dato") or "").lower()
        evidencia = flujo.get("evidencia")
        if calidad in {"estimado", "bajo"}:
            puntaje -= 15
        elif calidad in {"medio", "media"}:
            puntaje -= 8
        if not evidencia:
            puntaje -= 5
        if flujo.get("retorna_mismo_sistema_hidrico") in (None, ""):
            puntaje -= 5
    puntaje = max(0, min(100, puntaje))
    if puntaje >= 85:
        return "alta"
    if puntaje >= 60:
        return "media"
    return "baja"


def validar_resultado_no_duplicado(conn, empresa: str, sede_id: Any, periodo: Any) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM resultados_huella_agua
        WHERE empresa = %s AND sede_id = %s AND periodo = %s
        LIMIT 1
        """,
        (empresa, sede_id, periodo),
    )
    if cur.fetchone():
        raise HuellaAguaError("Ya existe un resultado para la misma empresa, sede y periodo.")


def consolidar_resultado_sede(
    flujos: Iterable[dict[str, Any]],
    sede: dict[str, Any],
    factores: Iterable[dict[str, Any] | FactorEscasez],
    periodo: Any,
    medida_productiva: Any = None,
) -> dict[str, Any]:
    flujos_lista = list(flujos)
    captacion = calcular_captacion_total(flujos_lista)
    retorno = calcular_retornos_totales(flujos_lista)
    retorno_mismo = calcular_retornos_mismo_sistema(flujos_lista)
    reuso = calcular_reuso_interno(flujos_lista)
    consumo = calcular_consumo_operativo_estimado(captacion, retorno_mismo, reuso)
    factor = seleccionar_factor_para_sede(factores, sede, periodo)
    huella = calcular_huella_escasez(consumo, factor) if factor else None
    nivel = clasificar_resultado_huella(consumo, factor, datos_suficientes=bool(flujos_lista))

    intensidad = None
    intensidad_escasez = None
    intensidad_total = None
    if medida_productiva not in (None, "", 0, "0"):
        try:
            medida = Decimal(str(medida_productiva))
            if medida > 0:
                intensidad = consumo / medida
                if huella is not None:
                    intensidad_escasez = huella / medida
        except Exception:
            intensidad = None

    return {
        "captacion_m3": captacion,
        "retorno_m3": retorno,
        "retorno_mismo_sistema_m3": retorno_mismo,
        "reuso_m3": reuso,
        "consumo_operativo_m3": consumo,
        "factor_escasez_aplicado": factor.factor_m3eq_m3 if factor else None,
        "huella_escasez_m3eq": huella,
        "id_factor_escasez": factor.id if factor else None,
        "nivel_calculo": nivel,
        "intensidad_hidrica": intensidad,
        "intensidad_escasez": intensidad_escasez,
        "factor": factor,
    }


def construir_reporte_huella(
    empresa: str,
    periodo: Any,
    flujos: list[dict[str, Any]],
    sedes: list[dict[str, Any]],
    factores: list[dict[str, Any] | FactorEscasez],
    medida_productiva: Any = None,
    vista: str = "mensual",
) -> dict[str, Any]:
    sedes_map = {s["id"]: s for s in sedes}
    grupos: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for flujo in flujos:
        periodo_flujo = str(flujo.get("periodo"))[:7] if vista == "mensual" else str(flujo.get("periodo"))[:4]
        grupos.setdefault((flujo.get("sede_id"), periodo_flujo), []).append(flujo)

    resultados = []
    for (sede_id, periodo_sel), grupo in grupos.items():
        sede = sedes_map.get(sede_id, {"id": sede_id, "nombre_sede": f"Sede {sede_id}"})
        consolidado = consolidar_resultado_sede(grupo, sede, factores, periodo_sel, medida_productiva)
        resultados.append(
            {
                "Empresa": empresa,
                "Sede": sede.get("nombre_sede"),
                "Período": periodo_sel,
                "Captación [m³]": consolidado["captacion_m3"],
                "Retorno [m³]": consolidado["retorno_m3"],
                "Retorno mismo sistema [m³]": consolidado["retorno_mismo_sistema_m3"],
                "Reúso [m³]": consolidado["reuso_m3"],
                "Consumo operativo [m³]": consolidado["consumo_operativo_m3"],
                "Factor [m³-eq/m³]": consolidado["factor_escasez_aplicado"],
                "Huella [m³-eq]": consolidado["huella_escasez_m3eq"],
                "Nivel": consolidado["nivel_calculo"],
                "Intensidad hídrica [m³/unidad]": consolidado["intensidad_hidrica"] if consolidado["intensidad_hidrica"] is not None else "No disponible",
                "Intensidad de escasez [m³-eq/unidad]": consolidado["intensidad_escasez"] if consolidado["intensidad_escasez"] is not None else "No disponible",
            }
        )

    resumen = [
        {"Indicador": "Captación [m³]", "Unidad": "m³", "Valor": sum(float(r["Captación [m³]"] or 0) for r in resultados)},
        {"Indicador": "Retorno [m³]", "Unidad": "m³", "Valor": sum(float(r["Retorno [m³]"] or 0) for r in resultados)},
        {"Indicador": "Reúso [m³]", "Unidad": "m³", "Valor": sum(float(r["Reúso [m³]"] or 0) for r in resultados)},
        {"Indicador": "Consumo operativo estimado [m³]", "Unidad": "m³", "Valor": sum(float(r["Consumo operativo [m³]"] or 0) for r in resultados)},
    ]
    resultados_con_consumo = [r for r in resultados if float(r["Consumo operativo [m³]"] or 0) > 0]
    huella_completa = bool(resultados_con_consumo) and all(
        r["Huella [m³-eq]"] not in (None, "No disponible") for r in resultados_con_consumo
    )
    resumen.append(
        {
            "Indicador": "Huella de escasez [m³-eq]",
            "Unidad": "m³-eq",
            "Valor": sum(float(r["Huella [m³-eq]"] or 0) for r in resultados_con_consumo) if huella_completa else "No disponible",
        }
    )

    factores_aplicados = []
    for f in factores:
        parsed = validar_factor_escasez(f)
        factores_aplicados.append(
            {
                "Método": parsed.metodo,
                "Versión": parsed.version_metodo,
                "Actividad": parsed.actividad,
                "Nivel geográfico": parsed.nivel_geografico,
                "Código geográfico": parsed.codigo_geografico,
                "Factor [m³-eq/m³]": parsed.factor_m3eq_m3,
                "Vigencia inicio": parsed.periodo_inicio,
                "Vigencia fin": parsed.periodo_fin,
                "Fuente": parsed.fuente,
                "Referencia": parsed.referencia,
                "Fecha de carga": parsed.fecha_carga,
            }
        )

    cobertura = []
    for sede in sedes:
        has_data = any(f.get("sede_id") == sede.get("id") for f in flujos)
        factor = seleccionar_factor_para_sede(factores, sede, periodo)
        cobertura.append(
            {
                "Empresa": empresa,
                "Sede": sede.get("nombre_sede"),
                "Ubicación": "Completa"
                if sede.get("codigo_cuenca") and sede.get("nombre_cuenca") and sede.get("region") and sede.get("comuna")
                else "Parcial"
                if sede.get("region") or sede.get("comuna")
                else "Pendiente",
                "Factor disponible": "Sí" if factor else "No",
                "Resultado": "Huella de escasez calculada"
                if factor and has_data
                else ("Solo inventario físico disponible" if has_data else "Datos insuficientes"),
                "Calidad": generar_indicador_calidad_datos([f for f in flujos if f.get("sede_id") == sede.get("id")]) if has_data else "baja",
            }
        )

    metodologia = [
        {"Sección": "Empresa", "Contenido": empresa},
        {"Sección": "Período", "Contenido": periodo or vista},
        {"Sección": "Sedes incluidas", "Contenido": ", ".join([s.get("nombre_sede") for s in sedes]) or "Sin sedes"},
        {
            "Sección": "Fuentes de agua consideradas",
            "Contenido": ", ".join(sorted({f.get("fuente_agua") or "Sin fuente" for f in flujos if f.get("tipo_flujo") == "captacion"}))
            or "Sin fuentes",
        },
        {"Sección": "Supuestos", "Contenido": "La huella de escasez solo se calcula cuando existe un factor válido configurado."},
        {"Sección": "Exclusiones", "Contenido": "Cadena de valor, agua indirecta y evaluación de calidad de agua."},
        {"Sección": "Calidad de datos", "Contenido": generar_indicador_calidad_datos(flujos) if flujos else "baja"},
        {"Sección": "Fecha de cálculo", "Contenido": "generada por el sistema"},
        {"Sección": "Versión del cálculo", "Contenido": SERVICE_VERSION},
        {
            "Sección": "Mensaje obligatorio",
            "Contenido": "Este reporte presenta una estimación operacional simplificada basada en metodología Water Footprint para operaciones directas. No constituye una verificación externa ni una evaluación completa de ciclo de vida.",
        },
    ]

    return {
        "Resumen": resumen,
        "Flujos de agua": flujos,
        "Resultados por sede": resultados,
        "Intensidades": [
            {
                "Sede": r["Sede"],
                "Período": r["Período"],
                "Intensidad hídrica [m³/unidad]": r["Intensidad hídrica [m³/unidad]"],
                "Intensidad de escasez [m³-eq/unidad]": r["Intensidad de escasez [m³-eq/unidad]"],
            }
            for r in resultados
        ]
        or [
            {
                "Sede": "No disponible",
                "Período": "No disponible",
                "Intensidad hídrica [m³/unidad]": "No disponible",
                "Intensidad de escasez [m³-eq/unidad]": "No disponible",
            }
        ],
        "Factores de escasez aplicados": factores_aplicados or [{"Método": "No disponible"}],
        "Cobertura y calidad de datos": cobertura
        or [{"Empresa": empresa, "Sede": "Sin sedes", "Ubicación": "Pendiente", "Factor disponible": "No", "Resultado": "Datos insuficientes", "Calidad": "baja"}],
        "Metodología y supuestos": metodologia,
    }

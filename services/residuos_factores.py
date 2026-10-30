"""Resolución y validación de factores de emisión para residuos.

Este módulo no conoce Flask ni la base de datos. Recibe filas de catálogo y
devuelve una selección determinística, lo que permite probar todos los
factores sin depender de una conexión remota.
"""

from __future__ import annotations

import unicodedata
import math
from typing import Any, Iterable, Mapping, Optional


TRATAMIENTOS_CANONICOS = {
    "vertedero": "landfill",
    "relleno sanitario": "landfill",
    "disposicion final": "landfill",
    "monorelleno": "landfill",
    "landfill": "landfill",
    "reciclaje": "open-loop",
    "reciclado": "open-loop",
    "open-loop": "open-loop",
    "open loop": "open-loop",
    "closed-loop": "closed-loop",
    "closed loop": "closed-loop",
    "compostaje": "composting",
    "composting": "composting",
    "compost": "composting",
    "degradacion anaerobica": "anaerobic digestion",
    "digestion anaerobica": "anaerobic digestion",
    "anaerobic digestion": "anaerobic digestion",
    "combustion": "combustion",
    "incineracion": "combustion",
    "reutilizacion": "re-use",
    "re-use": "re-use",
}


def normalizar_residuo(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(texto.lower().strip().split())


def normalizar_tratamiento_residuo(valor: Any) -> str:
    texto = normalizar_residuo(valor)
    if not texto:
        return ""
    for alias, canonico in sorted(TRATAMIENTOS_CANONICOS.items(), key=lambda item: len(item[0]), reverse=True):
        if texto == alias or alias in texto:
            return canonico
    return texto


def _valor(fila: Mapping[str, Any], nombre: str, default: Any = None) -> Any:
    return fila.get(nombre, default)


def _anio(fila: Mapping[str, Any]) -> int:
    try:
        return int(_valor(fila, "anio", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _factor(fila: Mapping[str, Any]) -> Optional[float]:
    try:
        valor = float(_valor(fila, "factor"))
    except (TypeError, ValueError):
        return None
    return valor if math.isfinite(valor) and valor > 0 else None


def resolver_factor_residuo(
    categoria: Any,
    tratamiento: Any,
    anio: Any,
    factores: Iterable[Mapping[str, Any]],
    unidad: str = "tonne",
) -> Optional[dict[str, Any]]:
    """Resuelve un factor sin fallbacks implícitos.

    El año 0 representa un factor genérico y solo se usa si no existe un
    factor específico para el año solicitado. Un factor sin tratamiento solo
    aplica cuando la solicitud tampoco tiene tratamiento.
    """
    categoria_norm = normalizar_residuo(categoria)
    tratamiento_norm = normalizar_tratamiento_residuo(tratamiento)
    unidad_norm = normalizar_residuo(unidad)
    try:
        anio_solicitado = int(anio or 0)
    except (TypeError, ValueError):
        anio_solicitado = 0

    tratamientos_a_probar = [tratamiento_norm]
    if tratamiento_norm == "open-loop":
        # Regla de negocio: Reciclaje siempre solicita Open-loop; Closed-loop
        # solo se usa como respaldo cuando no existe un Open-loop válido.
        tratamientos_a_probar.append("closed-loop")

    for tratamiento_aplicado in tratamientos_a_probar:
        candidatos: list[dict[str, Any]] = []
        for fila_original in factores:
            fila = dict(fila_original)
            if normalizar_residuo(_valor(fila, "categoria")) != categoria_norm:
                continue
            if normalizar_residuo(_valor(fila, "unidad", "")) != unidad_norm:
                continue
            if normalizar_tratamiento_residuo(_valor(fila, "tratamiento", "")) != tratamiento_aplicado:
                continue
            if _factor(fila) is None:
                continue
            candidatos.append(fila)

        especificos = [fila for fila in candidatos if _anio(fila) == anio_solicitado and anio_solicitado != 0]
        genericos = [fila for fila in candidatos if _anio(fila) == 0]
        elegibles = especificos or genericos
        if len(elegibles) > 1:
            # No se permite resolver ambigüedades mediante un fallback.
            return None
        if len(elegibles) != 1:
            continue

        resultado = elegibles[0]
        resultado["tratamiento_normalizado"] = tratamiento_norm
        resultado["tratamiento_aplicado"] = tratamiento_aplicado
        resultado["usando_fallback_tratamiento"] = tratamiento_aplicado != tratamiento_norm
        resultado["tratamiento_defra"] = resultado.get("tratamiento") or tratamiento_aplicado
        resultado["anio_aplicado"] = _anio(resultado)
        resultado["unidad_factor"] = unidad_norm
        resultado["es_generico"] = not bool(especificos)
        return resultado

    # Cero resultados = falta de factor.
    return None


def calcular_emision_residuo(cantidad_kg: Any, factor_kg_co2e_t: Any) -> Optional[float]:
    try:
        cantidad = float(cantidad_kg)
        factor = float(factor_kg_co2e_t)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(cantidad) or not math.isfinite(factor) or cantidad < 0 or factor < 0:
        return None
    return round((cantidad / 1000.0) * factor, 4)


def auditar_factores_residuos(factores: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Audita todas las filas del catálogo y sus claves recuperables."""
    filas = [dict(fila) for fila in factores]
    errores: list[str] = []
    claves: dict[tuple[Any, ...], int] = {}
    validos = 0

    for indice, fila in enumerate(filas, start=1):
        categoria = normalizar_residuo(_valor(fila, "categoria"))
        tratamiento = normalizar_tratamiento_residuo(_valor(fila, "tratamiento", ""))
        unidad = normalizar_residuo(_valor(fila, "unidad", ""))
        anio = _anio(fila)
        factor = _factor(fila)
        if not categoria or not tratamiento or unidad != "tonne" or anio < 0 or factor is None:
            errores.append(f"fila {indice}: campos inválidos")
            continue
        clave = (categoria, tratamiento, anio, unidad)
        claves[clave] = claves.get(clave, 0) + 1
        validos += 1
        seleccionado = resolver_factor_residuo(
            fila["categoria"], fila["tratamiento"], fila.get("anio", 0), [fila], fila["unidad"]
        )
        if seleccionado is None:
            errores.append(f"fila {indice}: no es recuperable por su propia clave")

    duplicados = [clave for clave, cantidad in claves.items() if cantidad > 1]
    errores.extend(f"clave duplicada: {clave}" for clave in duplicados)
    return {
        "total": len(filas),
        "validos": validos,
        "duplicados": duplicados,
        "errores": errores,
        "ok": not errores,
    }

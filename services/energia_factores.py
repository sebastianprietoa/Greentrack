"""Resolucion y normalizacion de factores energeticos Codex v1."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any


REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "factores_energia_chile_codex_v1.json"
MEDIA_TENSION_WARNING = "Nivel de tension historico no informado; se asumio media tension para el recalculo."


def _normalizar_texto(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = (
        text.replace("diésel", "diesel")
        .replace("combustión", "combustion")
        .replace("tensión", "tension")
        .replace("eléctrica", "electrica")
        .replace("eléctrico", "electrico")
    )
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("/", "-")
    return re.sub(r"\s+", " ", text)


@lru_cache(maxsize=1)
def cargar_registro() -> dict[str, Any]:
    with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _aliases(registry: dict[str, Any]) -> dict[str, str | None]:
    return {_normalizar_texto(row.get("alias_ui")): row.get("energy_id") for row in registry.get("aliases", [])}


def _resolver_energy_id(texto: str, registry: dict[str, Any]) -> str | None:
    aliases = _aliases(registry)
    for alias, energy_id in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if alias and alias in texto:
            return energy_id
    if "electricidad" in texto or "red electrica" in texto or "sen" in texto:
        return None
    if "diesel" in texto or "petroleo diesel" in texto or "di sel" in texto:
        return "diesel_fossil"
    if "gas natural" in texto:
        return "natural_gas"
    if "glp" in texto or "gas licuado" in texto:
        return "lpg"
    if "bencina" in texto or "gasolina" in texto:
        return "petrol"
    if "carbon" in texto:
        return "hard_coal"
    return None


def _resolver_electricidad(texto: str, tension: str | None) -> tuple[str, str | None]:
    tension_text = _normalizar_texto(tension)
    combined = f"{texto} {tension_text}"
    if any(token in combined for token in ("alta", " at", "hv", "high")):
        return "electricity_hv_cl", None
    if any(token in combined for token in ("baja", " bt", "lv", "low")):
        return "electricity_lv_cl", None
    if any(token in combined for token in ("media", " mt", "mv", "medium")):
        return "electricity_mv_cl", None
    return "electricity_mv_cl", MEDIA_TENSION_WARNING


def _entity_map(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["energy_id"]: row for row in registry.get("energy_entities", [])}


def resolver_factor(
    fuente: Any,
    categoria: Any,
    unidad: Any,
    origen_energia: Any = None,
    sistema: Any = None,
    nivel_tension: Any = None,
) -> dict[str, Any]:
    registry = cargar_registro()
    combined = " ".join(_normalizar_texto(value) for value in (fuente, categoria, origen_energia, sistema))
    is_electricity = "electricidad" in combined or "red electrica" in combined or "sen" in combined
    warning = None
    if is_electricity:
        energy_id, warning = _resolver_electricidad(combined, nivel_tension)
    else:
        energy_id = _resolver_energy_id(combined, registry)

    entity = _entity_map(registry).get(energy_id) if energy_id else None
    if not entity:
        return {"energy_id": None, "water_status": "not_applicable", "warning": "No se pudo resolver un energy_id."}

    result = {
        "energy_id": energy_id,
        "water_factor_id": entity.get("water_factor_id"),
        "water_status": entity.get("water_status") or "unknown",
        "warning": warning,
        "carbon_policy": entity.get("carbon_policy"),
        "carbon_context": entity.get("carbon_context"),
        "accepted_input_units": entity.get("accepted_input_units") or [],
        "conversion_key": entity.get("conversion_key"),
    }
    water_factor = entity.get("water_factor") or {}
    result.update(
        {
            "factor_value": water_factor.get("value"),
            "factor_unit": water_factor.get("unit"),
            "source_unit": water_factor.get("source_unit"),
            "dataset": water_factor.get("dataset"),
            "geography": water_factor.get("geography"),
            "quality": water_factor.get("quality"),
            "method": water_factor.get("indicator"),
            "source_id": water_factor.get("source_id"),
            "system_model": water_factor.get("system_model"),
            "infrastructure_excluded": water_factor.get("infrastructure_excluded"),
            "conversion": entity.get("conversion") or {},
        }
    )
    return result


def normalizar_cantidad(cantidad: Any, unidad: Any, factor: dict[str, Any]) -> tuple[float | None, str | None]:
    try:
        quantity = float(cantidad)
    except (TypeError, ValueError):
        return None, "Cantidad no numerica."
    if not math.isfinite(quantity) or quantity < 0:
        return None, "La cantidad debe ser finita y no negativa."

    source_unit = str(factor.get("source_unit") or "").casefold()
    input_unit = _normalizar_texto(unidad)
    conversion = factor.get("conversion") or {}
    if input_unit in ("mwh",):
        input_unit = "mwh"
    elif input_unit in ("kwh", "kw h", "kilowatt hour", "kilowatt-hour"):
        input_unit = "kwh"
    elif input_unit in ("l", "lt", "lts", "litro", "litros", "liter", "liters"):
        input_unit = "l"
    elif input_unit in ("m3", "metro cubico", "metros cubicos"):
        input_unit = "m3"
    elif input_unit in ("t", "ton", "tonelada", "toneladas"):
        input_unit = "t"
    elif input_unit in ("kg", "kilogramo", "kilogramos"):
        input_unit = "kg"
    elif input_unit in ("mj", "megajoule", "megajoules"):
        input_unit = "mj"
    elif input_unit in ("gj", "gigajoule", "gigajoules"):
        input_unit = "gj"
    elif input_unit == "gal_us":
        input_unit = "gal_us"

    if input_unit not in {"kg", "t", "l", "m3", "gal_us", "mj", "gj", "kwh", "mwh"}:
        return None, "Unidad no soportada para el factor entregado."

    if source_unit == "kwh":
        if input_unit == "kwh":
            return quantity, None
        if input_unit == "mwh":
            return quantity * 1000, None
        if input_unit == "mj":
            return quantity / 3.6, None
        if input_unit == "gj":
            return quantity * (1000 / 3.6), None
        return None, "Electricidad requiere kWh, MWh, MJ o GJ."

    if source_unit == "m3":
        pcs = conversion.get("pcs_MJ_per_m3")
        if input_unit == "m3":
            return quantity, None
        if input_unit == "l":
            return quantity / 1000, None
        if pcs and input_unit == "mj":
            return quantity / float(pcs), None
        if pcs and input_unit == "gj":
            return quantity * 1000 / float(pcs), None
        if pcs and input_unit == "kwh":
            return quantity * 3.6 / float(pcs), None
        if pcs and input_unit == "mwh":
            return quantity * 3600 / float(pcs), None
        return None, "Falta PCS para convertir a m3 de gas."

    if source_unit == "kg":
        density = conversion.get("density_kg_per_L")
        pcs = conversion.get("pcs_MJ_per_kg")
        if input_unit == "kg":
            return quantity, None
        if input_unit == "t":
            return quantity * 1000, None
        if density and input_unit == "l":
            return quantity * float(density), None
        if density and input_unit == "m3":
            return quantity * 1000 * float(density), None
        if density and input_unit == "gal_us":
            return quantity * 3.785411784 * float(density), None
        if pcs and input_unit == "mj":
            return quantity / float(pcs), None
        if pcs and input_unit == "gj":
            return quantity * 1000 / float(pcs), None
        if pcs and input_unit == "kwh":
            return quantity * 3.6 / float(pcs), None
        if pcs and input_unit == "mwh":
            return quantity * 3600 / float(pcs), None
        return None, "Falta densidad o PCS para convertir a kg."

    return None, "Unidad fuente no configurada."


def calcular_suministro(cantidad: Any, unidad: Any, factor: dict[str, Any]) -> dict[str, Any]:
    status = factor.get("water_status") or "unknown"
    if status in {"requires_blend", "requires_waste_model"}:
        return {"normalized_quantity": None, "water_m3": None, "warning": f"Calculo bloqueado por estado {status}."}
    if not factor.get("water_factor_id") or factor.get("factor_value") is None:
        return {"normalized_quantity": None, "water_m3": None, "warning": "No hay factor hidrico aplicable."}
    normalized, warning = normalizar_cantidad(cantidad, unidad, factor)
    if normalized is None:
        return {"normalized_quantity": None, "water_m3": None, "warning": warning}
    water_m3 = normalized * float(factor["factor_value"])
    return {"normalized_quantity": normalized, "water_m3": water_m3, "warning": warning or factor.get("warning")}

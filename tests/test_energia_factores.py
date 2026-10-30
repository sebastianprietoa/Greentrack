from services.energia_factores import calcular_suministro, resolver_factor


def test_diesel_1000_litros_usa_factor_freshwater():
    factor = resolver_factor("Combustión Fija", "Diésel", "litros")
    resultado = calcular_suministro(1000, "litros", factor)
    assert factor["energy_id"] == "diesel_fossil"
    assert round(resultado["water_m3"], 8) == 1.35210516


def test_electricidad_media_10_mwh():
    factor = resolver_factor("Electricidad", "Electricidad SEN", "MWh", nivel_tension="media")
    resultado = calcular_suministro(10, "MWh", factor)
    assert factor["water_factor_id"] == "water_electricity_mv_cl"
    assert round(resultado["water_m3"], 8) == 10.135227


def test_electricidad_sin_tension_asume_media_con_advertencia():
    factor = resolver_factor("Electricidad", "Red Eléctrica", "kWh")
    assert factor["water_factor_id"] == "water_electricity_mv_cl"
    assert factor["warning"]


def test_mezcla_requiere_revision():
    factor = resolver_factor("Combustión", "Diésel B5/B10", "litros")
    resultado = calcular_suministro(100, "litros", factor)
    assert factor["water_status"] == "requires_blend"
    assert resultado["water_m3"] is None

import unittest

from services.residuos_factores import (
    auditar_factores_residuos,
    calcular_emision_residuo,
    normalizar_tratamiento_residuo,
    resolver_factor_residuo,
)


class ResiduosFactoresTests(unittest.TestCase):
    def setUp(self):
        self.factores = [
            {
                "categoria": "Household residual waste",
                "tratamiento": "Landfill",
                "anio": 2025,
                "unidad": "tonne",
                "factor": 420.0,
                "nombre_chile": "Residuos domiciliarios residuales",
            },
            {
                "categoria": "Household residual waste",
                "tratamiento": "Open-loop",
                "anio": 2025,
                "unidad": "tonne",
                "factor": 12.0,
            },
            {
                "categoria": "Commercial and industrial waste",
                "tratamiento": "Vertedero",
                "anio": 2025,
                "unidad": "tonne",
                "factor": 310.0,
            },
            {
                "categoria": "Commercial and industrial waste",
                "tratamiento": "Vertedero",
                "anio": 0,
                "unidad": "tonne",
                "factor": 300.0,
            },
        ]

    def test_treatment_aliases_are_canonical(self):
        self.assertEqual(normalizar_tratamiento_residuo("Relleno sanitario"), "landfill")
        self.assertEqual(normalizar_tratamiento_residuo("Landfill"), "landfill")
        self.assertEqual(normalizar_tratamiento_residuo("Reciclaje de plásticos"), "open-loop")
        self.assertEqual(normalizar_tratamiento_residuo("Compostaje"), "composting")
        self.assertEqual(normalizar_tratamiento_residuo("Digestión anaeróbica"), "anaerobic digestion")

    def test_landfill_does_not_fallback_to_recycling(self):
        selected = resolver_factor_residuo(
            "Household residual waste", "Vertedero", 2025, self.factores
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected["factor"], 420.0)

    def test_specific_year_precedes_generic_year(self):
        selected = resolver_factor_residuo(
            "Commercial and industrial waste", "Relleno sanitario", 2025, self.factores
        )
        self.assertEqual(selected["factor"], 310.0)
        selected = resolver_factor_residuo(
            "Commercial and industrial waste", "Relleno sanitario", 2026, self.factores
        )
        self.assertEqual(selected["factor"], 300.0)

    def test_missing_treatment_does_not_select_any_category_factor(self):
        selected = resolver_factor_residuo(
            "Household residual waste", "", 2025, self.factores
        )
        self.assertIsNone(selected)

    def test_recycling_prefers_open_loop(self):
        selected = resolver_factor_residuo(
            "Plastic", "Reciclaje", 2024,
            [
                {"categoria": "Plastic", "tratamiento": "Open-loop", "unidad": "tonne", "factor": 2.0, "anio": 0},
                {"categoria": "Plastic", "tratamiento": "Closed-loop", "unidad": "tonne", "factor": 3.0, "anio": 0},
            ],
        )
        self.assertEqual(selected["factor"], 2.0)
        self.assertEqual(selected["tratamiento_aplicado"], "open-loop")
        self.assertFalse(selected["usando_fallback_tratamiento"])

    def test_recycling_falls_back_to_closed_loop(self):
        selected = resolver_factor_residuo(
            "Plastic", "Reciclaje", 2024,
            [{"categoria": "Plastic", "tratamiento": "Closed-loop", "unidad": "tonne", "factor": 3.0, "anio": 0}],
        )
        self.assertEqual(selected["factor"], 3.0)
        self.assertEqual(selected["tratamiento_aplicado"], "closed-loop")
        self.assertTrue(selected["usando_fallback_tratamiento"])

    def test_recycling_does_not_fallback_when_open_loop_is_ambiguous(self):
        selected = resolver_factor_residuo(
            "Plastic", "Reciclaje", 2024,
            [
                {"categoria": "Plastic", "tratamiento": "Open-loop", "unidad": "tonne", "factor": 2.0, "anio": 0},
                {"categoria": "Plastic", "tratamiento": "Open-loop", "unidad": "tonne", "factor": 2.1, "anio": 0},
                {"categoria": "Plastic", "tratamiento": "Closed-loop", "unidad": "tonne", "factor": 3.0, "anio": 0},
            ],
        )
        self.assertIsNone(selected)

    def test_unit_is_part_of_the_lookup_key(self):
        selected = resolver_factor_residuo(
            "Household residual waste", "Vertedero", 2025, self.factores, unidad="kg"
        )
        self.assertIsNone(selected)

    def test_emission_uses_kg_to_tonne_conversion(self):
        self.assertEqual(calcular_emision_residuo(1000, 420), 420.0)

    def test_every_catalog_factor_is_recoverable(self):
        reporte = auditar_factores_residuos(self.factores)
        self.assertTrue(reporte["ok"], reporte)
        self.assertEqual(reporte["total"], reporte["validos"])

    def test_duplicate_catalog_key_is_rejected(self):
        factores = self.factores + [dict(self.factores[0])]
        reporte = auditar_factores_residuos(factores)
        self.assertFalse(reporte["ok"])
        self.assertTrue(reporte["duplicados"])


if __name__ == "__main__":
    unittest.main()

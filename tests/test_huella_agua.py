import unittest
from decimal import Decimal

from services.huella_agua import (
    HuellaAguaError,
    construir_reporte_huella,
    buscar_factor_escasez_mas_especifico,
    calcular_captacion_total,
    calcular_consumo_operativo_estimado,
    calcular_huella_escasez,
    calcular_retornos_mismo_sistema,
    calcular_retornos_totales,
    calcular_reuso_interno,
    clasificar_resultado_huella,
    generar_indicador_calidad_datos,
    validar_factor_escasez,
    validar_resultado_no_duplicado,
)


class FakeCursor:
    def __init__(self, existing=False):
        self.existing = existing
        self.query = None
        self.params = None

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchone(self):
        return (1,) if self.existing else None


class FakeConn:
    def __init__(self, existing=False):
        self.cursor_obj = FakeCursor(existing=existing)

    def cursor(self):
        return self.cursor_obj


class HuellaAguaServiceTests(unittest.TestCase):
    def setUp(self):
        self.flujos = [
            {"tipo_flujo": "captacion", "volumen_m3": "100", "calidad_dato": "alta", "evidencia": "medidor", "retorna_mismo_sistema_hidrico": False},
            {"tipo_flujo": "retorno", "volumen_m3": "30", "calidad_dato": "alta", "evidencia": "planta", "retorna_mismo_sistema_hidrico": True},
            {"tipo_flujo": "reuso", "volumen_m3": "10", "calidad_dato": "media", "evidencia": "registro", "retorna_mismo_sistema_hidrico": False},
        ]

    def test_captacion_sin_retorno(self):
        captacion = calcular_captacion_total([self.flujos[0]])
        retornos = calcular_retornos_totales([self.flujos[0]])
        retorno_mismo = calcular_retornos_mismo_sistema([self.flujos[0]])
        reuso = calcular_reuso_interno([self.flujos[0]])
        consumo = calcular_consumo_operativo_estimado(captacion, retorno_mismo, reuso)

        self.assertEqual(captacion, Decimal("100"))
        self.assertEqual(retornos, Decimal("0"))
        self.assertEqual(consumo, Decimal("100"))

    def test_captacion_con_retorno_al_mismo_sistema(self):
        captacion = calcular_captacion_total(self.flujos)
        retorno_mismo = calcular_retornos_mismo_sistema(self.flujos)
        consumo = calcular_consumo_operativo_estimado(captacion, retorno_mismo, calcular_reuso_interno(self.flujos))

        self.assertEqual(captacion, Decimal("100"))
        self.assertEqual(retorno_mismo, Decimal("30"))
        self.assertEqual(consumo, Decimal("70"))

    def test_captacion_con_retorno_a_sistema_distinto(self):
        flujos = [
            {"tipo_flujo": "captacion", "volumen_m3": "80", "calidad_dato": "alta", "evidencia": "medidor", "retorna_mismo_sistema_hidrico": False},
            {"tipo_flujo": "retorno", "volumen_m3": "20", "calidad_dato": "alta", "evidencia": "planta", "retorna_mismo_sistema_hidrico": False},
        ]
        captacion = calcular_captacion_total(flujos)
        retorno_mismo = calcular_retornos_mismo_sistema(flujos)
        retornos = calcular_retornos_totales(flujos)

        self.assertEqual(captacion, Decimal("80"))
        self.assertEqual(retorno_mismo, Decimal("0"))
        self.assertEqual(retornos, Decimal("20"))
        self.assertEqual(calcular_consumo_operativo_estimado(captacion, retorno_mismo), Decimal("80"))

    def test_reuso_interno(self):
        reuso = calcular_reuso_interno(self.flujos)
        self.assertEqual(reuso, Decimal("10"))

    def test_sede_sin_factor(self):
        consumo = Decimal("70")
        self.assertIsNone(calcular_huella_escasez(consumo, None))
        self.assertEqual(clasificar_resultado_huella(consumo, None), "Solo inventario físico disponible")

    def test_sede_con_factor(self):
        factor = validar_factor_escasez({
            "id": 1,
            "metodo": "Alineado con principios de ISO 14046",
            "version_metodo": "v1",
            "actividad": "Agua",
            "nivel_geografico": "cuenca",
            "codigo_geografico": "C-01",
            "periodo_inicio": "2025-01-01",
            "periodo_fin": "2025-12-31",
            "factor_m3eq_m3": "0.5",
            "fuente": "Fuente oficial",
            "referencia": "Doc 1",
            "fecha_carga": "2025-01-15",
            "activo": True,
        })
        huella = calcular_huella_escasez(Decimal("70"), factor)
        self.assertEqual(huella, Decimal("35.0"))
        self.assertEqual(clasificar_resultado_huella(Decimal("70"), factor), "Huella de escasez calculada")

    def test_validacion_datos_negativos(self):
        with self.assertRaises(HuellaAguaError):
            calcular_captacion_total([{"tipo_flujo": "captacion", "volumen_m3": "-1"}])
        with self.assertRaises(HuellaAguaError):
            validar_factor_escasez({
                "metodo": "M",
                "version_metodo": "1",
                "actividad": "A",
                "nivel_geografico": "cuenca",
                "codigo_geografico": "X",
                "periodo_inicio": "2025-01-01",
                "periodo_fin": "2025-12-31",
                "factor_m3eq_m3": "0",
                "fuente": "F",
                "referencia": "R",
                "fecha_carga": "2025-01-01",
                "activo": True,
            })

    def test_retorno_supera_captacion(self):
        with self.assertRaises(HuellaAguaError):
            calcular_consumo_operativo_estimado(Decimal("10"), Decimal("15"))

    def test_busqueda_factor_mas_especifico(self):
        factores = [
            {
                "id": 1,
                "metodo": "Alineado con principios de ISO 14046",
                "version_metodo": "v1",
                "actividad": "Agua",
                "nivel_geografico": "pais",
                "codigo_geografico": "Chile",
                "periodo_inicio": "2025-01-01",
                "periodo_fin": "2025-12-31",
                "factor_m3eq_m3": "0.2",
                "fuente": "Nacional",
                "referencia": "Ref",
                "fecha_carga": "2025-01-01",
                "activo": True,
            },
            {
                "id": 2,
                "metodo": "Alineado con principios de ISO 14046",
                "version_metodo": "v1",
                "actividad": "Agua",
                "nivel_geografico": "cuenca",
                "codigo_geografico": "C-01",
                "periodo_inicio": "2025-01-01",
                "periodo_fin": "2025-12-31",
                "factor_m3eq_m3": "0.7",
                "fuente": "Cuenca",
                "referencia": "Ref",
                "fecha_carga": "2025-01-01",
                "activo": True,
            },
        ]
        sede = {"codigo_cuenca": "C-01", "region": "RM", "comuna": "Santiago", "pais": "Chile"}
        factor = buscar_factor_escasez_mas_especifico(factores, sede, "2025-06-01")
        self.assertEqual(factor.id, 2)

    def test_jerarquia_factor_cuenca_subnacional_pais(self):
        factores = [
            {"id": 1, "metodo": "M", "version_metodo": "1", "actividad": "A", "nivel_geografico": "pais", "codigo_geografico": "Chile", "periodo_inicio": "2025-01-01", "periodo_fin": "2025-12-31", "factor_m3eq_m3": "0.2", "fuente": "Nacional", "referencia": "R", "fecha_carga": "2025-01-01", "activo": True},
            {"id": 2, "metodo": "M", "version_metodo": "1", "actividad": "A", "nivel_geografico": "subnacional", "codigo_geografico": "RM", "periodo_inicio": "2025-01-01", "periodo_fin": "2025-12-31", "factor_m3eq_m3": "0.3", "fuente": "Regional", "referencia": "R", "fecha_carga": "2025-01-01", "activo": True},
            {"id": 3, "metodo": "M", "version_metodo": "1", "actividad": "A", "nivel_geografico": "cuenca", "codigo_geografico": "C-01", "periodo_inicio": "2025-01-01", "periodo_fin": "2025-12-31", "factor_m3eq_m3": "0.7", "fuente": "Cuenca", "referencia": "R", "fecha_carga": "2025-01-01", "activo": True},
        ]
        sede = {"codigo_cuenca": "C-01", "region": "RM", "comuna": "Santiago", "pais": "Chile"}
        factor = buscar_factor_escasez_mas_especifico(factores, sede, "2025-06-01")
        self.assertEqual(factor.id, 3)

    def test_reuso_no_se_descuenta_dos_veces(self):
        flujos = [
            {"tipo_flujo": "captacion", "volumen_m3": "100", "calidad_dato": "alta", "evidencia": "medidor", "retorna_mismo_sistema_hidrico": False},
            {"tipo_flujo": "reuso", "volumen_m3": "20", "calidad_dato": "alta", "evidencia": "registro", "retorna_mismo_sistema_hidrico": False},
        ]
        captacion = calcular_captacion_total(flujos)
        retorno_mismo = calcular_retornos_mismo_sistema(flujos)
        consumo = calcular_consumo_operativo_estimado(captacion, retorno_mismo, calcular_reuso_interno(flujos))
        self.assertEqual(consumo, Decimal("100"))

    def test_retorno_no_se_descuenta_si_no_es_mismo_sistema(self):
        flujos = [
            {"tipo_flujo": "captacion", "volumen_m3": "100", "calidad_dato": "alta", "evidencia": "medidor", "retorna_mismo_sistema_hidrico": False},
            {"tipo_flujo": "retorno", "volumen_m3": "25", "calidad_dato": "alta", "evidencia": "planta", "retorna_mismo_sistema_hidrico": False},
        ]
        self.assertEqual(calcular_retornos_mismo_sistema(flujos), Decimal("0"))
        self.assertEqual(calcular_consumo_operativo_estimado(Decimal("100"), Decimal("0")), Decimal("100"))

    def test_exportacion_sin_factor(self):
        sedes = [{"id": 1, "nombre_sede": "Casa Matriz", "region": "RM", "comuna": "Santiago", "codigo_cuenca": "C-01", "nombre_cuenca": "Cuenca 1"}]
        flujos = [{"sede_id": 1, "periodo": "2025-06-01", "tipo_flujo": "captacion", "fuente_agua": "Red pública", "destino_agua": None, "volumen_m3": "50", "proceso_o_area": "Operación", "retorna_mismo_sistema_hidrico": False, "tiene_tratamiento": False, "calidad_dato": "Medición directa", "evidencia": "Factura", "observaciones": ""}]
        reporte = construir_reporte_huella("Empresa", "2025-06", flujos, sedes, [], medida_productiva=None)
        self.assertEqual(reporte["Resultados por sede"][0]["Huella [m³-eq]"], None)
        self.assertEqual(reporte["Resumen"][0]["Unidad"], "m³")

    def test_exportacion_con_factor_y_medida(self):
        sedes = [{"id": 1, "nombre_sede": "Casa Matriz", "region": "RM", "comuna": "Santiago", "codigo_cuenca": "C-01", "nombre_cuenca": "Cuenca 1"}]
        flujos = [
            {"sede_id": 1, "periodo": "2025-06-01", "tipo_flujo": "captacion", "fuente_agua": "Red pública", "destino_agua": None, "volumen_m3": "100", "proceso_o_area": "Operación", "retorna_mismo_sistema_hidrico": False, "tiene_tratamiento": False, "calidad_dato": "Medición directa", "evidencia": "Factura", "observaciones": ""},
            {"sede_id": 1, "periodo": "2025-06-01", "tipo_flujo": "retorno", "fuente_agua": None, "destino_agua": "Sistema", "volumen_m3": "20", "proceso_o_area": "Operación", "retorna_mismo_sistema_hidrico": True, "tiene_tratamiento": True, "calidad_dato": "Medición directa", "evidencia": "Planta", "observaciones": ""},
        ]
        factores = [{"id": 9, "metodo": "M", "version_metodo": "1", "actividad": "A", "nivel_geografico": "cuenca", "codigo_geografico": "C-01", "periodo_inicio": "2025-01-01", "periodo_fin": "2025-12-31", "factor_m3eq_m3": "0.5", "fuente": "Oficial", "referencia": "R", "fecha_carga": "2025-01-01", "activo": True}]
        reporte = construir_reporte_huella("Empresa", "2025-06", flujos, sedes, factores, medida_productiva=10)
        self.assertEqual(reporte["Resultados por sede"][0]["Huella [m³-eq]"], Decimal("40.0"))
        self.assertEqual(reporte["Resultados por sede"][0]["Intensidad hídrica [m³/unidad]"], Decimal("8"))
        self.assertEqual(reporte["Factores de escasez aplicados"][0]["Factor [m³-eq/m³]"], Decimal("0.5"))

    def test_indicador_calidad_datos(self):
        calidad = generar_indicador_calidad_datos(self.flujos)
        self.assertIn(calidad, {"alta", "media"})

    def test_validacion_resultado_duplicado(self):
        conn = FakeConn(existing=True)
        with self.assertRaises(HuellaAguaError):
            validar_resultado_no_duplicado(conn, "Empresa", 1, "2025-01-01")

    def test_datos_negativos_en_reporte_no_rompen(self):
        with self.assertRaises(HuellaAguaError):
            calcular_consumo_operativo_estimado(Decimal("-10"), Decimal("0"))


if __name__ == "__main__":
    unittest.main()

"""Protección de medidas por año, con tablas temporales del clon local."""
import pytest

from tests.test_aceptacion_roles import acceptance_pg, water_pg, fuel_pg, pg, database, client_for, context_get


@pytest.fixture
def annual_pg(acceptance_pg):
    connection,_ = acceptance_pg
    cursor = connection.cursor()
    cursor.executemany('''INSERT INTO medidas_productivas
        (empresa,anio,unidad,valor,fecha_actualizacion) VALUES (%s,%s,%s,%s,'2020-01-01 00:00')''', [
        ('AUDIT_A',2025,'Personas',1200), ('AUDIT_A',2026,'Horas de trabajo',100),
        ('AUDIT_B',2025,'Personas',99)])
    cursor.execute("INSERT INTO configuracion (empresa,vehiculos) VALUES ('AUDIT_A',0)")
    connection.commit()
    return connection


def state(connection):
    cursor = connection.cursor()
    cursor.execute('SELECT empresa,anio,unidad,valor,fecha_actualizacion FROM medidas_productivas ORDER BY empresa,anio')
    return cursor.fetchall()


def post(connection, **changes):
    data = dict(medida_anio='2025', medida_unidad='Personas', medida_valor='1300', vehiculos='si')
    data.update(changes)
    with database(connection):
        client = client_for('empresa')
        response = client.post('/configuracion',data=data,follow_redirects=True)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_existing_measure_requires_confirmation_and_rolls_back_flags(annual_pg):
    before = state(annual_pg)
    assert 'Confirma el valor anterior y el nuevo' in post(annual_pg)
    assert state(annual_pg) == before
    cursor = annual_pg.cursor()
    cursor.execute("SELECT vehiculos FROM configuracion WHERE empresa='AUDIT_A'")
    assert cursor.fetchone()[0] == 0


def test_confirmed_replace_only_changes_selected_company_and_year(annual_pg):
    before = state(annual_pg)
    html = post(annual_pg, confirmar_medida='1', medida_anterior_unidad='Personas',medida_anterior_valor='1200')
    assert 'Configuración guardada correctamente' in html
    after = state(annual_pg)
    assert after[0][3] == 1300
    assert after[1:] == before[1:]


@pytest.mark.parametrize('old_unit,old_value',[('Horas de trabajo','1200'),('Personas','1100'),('','')])
def test_stale_or_wrong_confirmation_cannot_overwrite_measure(annual_pg,old_unit,old_value):
    before = state(annual_pg)
    post(annual_pg,confirmar_medida='1',medida_anterior_unidad=old_unit,medida_anterior_valor=old_value)
    assert state(annual_pg) == before


def test_save_unchanged_measure_preserves_value_and_update_date(annual_pg):
    before = state(annual_pg)
    assert 'Configuración guardada correctamente' in post(annual_pg,medida_valor='1200.00')
    assert state(annual_pg) == before
    cursor = annual_pg.cursor()
    cursor.execute("SELECT vehiculos FROM configuracion WHERE empresa='AUDIT_A'")
    assert cursor.fetchone()[0] == 1


def test_general_configuration_without_measure_preserves_all_years(annual_pg):
    before = state(annual_pg)
    assert 'Configuración guardada correctamente' in post(annual_pg,medida_unidad='',medida_valor='')
    assert state(annual_pg) == before


def test_new_year_does_not_overwrite_existing_years(annual_pg):
    before = state(annual_pg)
    assert 'Configuración guardada correctamente' in post(annual_pg,medida_anio='2024',medida_valor='500')
    after = state(annual_pg)
    assert after[0][1:4] == (2024,'Personas',500)
    assert after[1:] == before


@pytest.mark.parametrize('year',['0','10000','incorrecto'])
def test_invalid_year_preserves_measures(annual_pg,year):
    before = state(annual_pg)
    post(annual_pg,medida_anio=year)
    assert state(annual_pg) == before


def test_configuration_years_are_scoped_to_logged_company(annual_pg):
    with database(annual_pg):
        _,context = context_get(client_for('empresa'),'/configuracion?anio=2025')
    assert context['medida_actual']['valor'] == 1200
    assert {r['anio']:r['valor'] for r in context['medidas_historial']} == {2025:1200,2026:100}

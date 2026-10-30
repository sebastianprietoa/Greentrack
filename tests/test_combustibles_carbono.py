import json
import io
from unittest.mock import patch

from flask import template_rendered
import pytest

from services.combustibles_carbono import fecha_periodo, numero_finito, resolver_factor_carbono
from services.energia_factores import calcular_suministro, resolver_factor
from services.residuos_factores import calcular_emision_residuo
from tests.test_integridad_postgres import pg, SharedConnection


@pytest.fixture
def fuel_pg(pg):
    cursor = pg.cursor()
    # Usar el esquema real del clon, con secuencias temporales propias.
    cursor.execute('DROP TABLE pg_temp.registros, pg_temp.resultados_huella_suministro_agua, pg_temp.combustible_movil')
    cursor.execute('ALTER TABLE vehiculos ADD COLUMN patente TEXT, ADD COLUMN tipo TEXT')
    cursor.execute("UPDATE vehiculos SET patente='TEST01',tipo='auto'")
    for table in ['registros','resultados_huella_suministro_agua','combustible_movil']:
        cursor.execute(f'CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING DEFAULTS INCLUDING INDEXES)')
        cursor.execute(f'CREATE TEMP SEQUENCE test_{table}_seq START 1000')
        cursor.execute(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('pg_temp.test_{table}_seq')")
    cursor.execute('CREATE TEMP TABLE factores (categoria TEXT, unidad TEXT, factor NUMERIC, anio INTEGER, tratamiento TEXT)')
    cursor.executemany('INSERT INTO factores (categoria,unidad,factor,anio) VALUES (%s,%s,%s,%s)',[
        ('Diésel fósil puro','Litros',2.66155,0),
        ('Diésel fósil puro','m3',2661.55,0),
        ('Diésel fósil puro','kg',3.2039115,0),
        ('Diésel fósil puro','Toneladas',3203.9114,0),
    ])
    pg.commit()
    yield pg


@pytest.fixture
def electric_pg(fuel_pg):
    cursor = fuel_pg.cursor()
    cursor.execute('CREATE TEMP TABLE factores_electricos (sistema TEXT, anio INTEGER, mes INTEGER, factor_emision_avg NUMERIC)')
    cursor.executemany('INSERT INTO factores_electricos VALUES (%s,%s,%s,%s)',[
        ('SEN',2026,1,0.2),('SEN',2026,2,0.25),('SEN',2027,1,0.3),
    ])
    fuel_pg.commit()
    yield fuel_pg


def form_post(connection,path,data):
    import app as module
    module.app.config.update(TESTING=True)
    client = module.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,empresa='AUDIT_A',es_admin=0,rol='empresa')
    with patch.object(module,'get_db',return_value=SharedConnection(connection)):
        return client.post(path,data=data)


def mobile_post(connection, records, connection_wrapper=SharedConnection):
    import app as module
    module.app.config.update(TESTING=True)
    client = module.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,empresa='AUDIT_A',es_admin=0,rol='empresa')
    with patch.object(module,'get_db',return_value=connection_wrapper(connection)):
        response = client.post('/api/combustible/movil',json={'registros':records})
    return response


def record(**changes):
    return {'vehiculo_id':401,'periodo':'2026-01','cantidad':100,'unidad':'litros','combustible':'diesel',**changes}


def row_counts(connection):
    cursor = connection.cursor()
    counts = []
    for table in ['registros','combustible_movil','resultados_huella_suministro_agua']:
        cursor.execute(f'SELECT COUNT(*) FROM {table}')
        counts.append(cursor.fetchone()[0])
    return counts


def test_mobile_units_use_catalog_and_preserve_water_supply(fuel_pg):
    response = mobile_post(fuel_pg,[record(cantidad=1000),record(cantidad=1,unidad='m3')])
    assert response.json['guardados'] == 2
    cursor = fuel_pg.cursor()
    cursor.execute('SELECT emision FROM registros ORDER BY id')
    values = [float(row[0]) for row in cursor.fetchall()]
    assert values == pytest.approx([2661.55,2661.55])
    cursor.execute('SELECT huella_suministro_m3 FROM resultados_huella_suministro_agua ORDER BY registro_id')
    assert [float(row[0]) for row in cursor.fetchall()] == pytest.approx([1.35210516,1.35210516])


def test_failed_sql_row_keeps_prior_success_and_allows_following_row(fuel_pg):
    cursor = fuel_pg.cursor()
    cursor.execute('''CREATE FUNCTION pg_temp.reject_february() RETURNS trigger LANGUAGE plpgsql AS
        $$ BEGIN IF NEW.periodo='2026-02' THEN RAISE EXCEPTION 'test row failure'; END IF; RETURN NEW; END $$''')
    cursor.execute('CREATE TRIGGER reject_february BEFORE INSERT ON combustible_movil FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_february()')
    fuel_pg.commit()
    response = mobile_post(fuel_pg,[record(),record(periodo='2026-02'),record(periodo='2026-03')])
    assert response.json['guardados'] == 2
    assert len(response.json['errores']) == 1
    assert row_counts(fuel_pg) == [2,2,2]
    cursor.execute('SELECT fecha FROM registros ORDER BY fecha')
    assert [str(row[0]) for row in cursor.fetchall()] == ['2026-01-01','2026-03-01']


@pytest.mark.parametrize('changes',[
    {'cantidad':-1},{'cantidad':'nan'},{'cantidad':'inf'},
    {'costo':-1},{'periodo':'2026-13'},{'periodo':'2026-1'},
    {'vehiculo_id':402},{'combustible':'otro'},{'combustible':'electricidad'},
    {'unidad':'kwh'},
])
def test_invalid_mobile_input_does_not_save(fuel_pg,changes):
    response = mobile_post(fuel_pg,[record(**changes)])
    assert response.json['success'] is False
    assert row_counts(fuel_pg) == [0,0,0]


def test_commit_failure_is_not_reported_as_success(fuel_pg):
    class CommitFailure(SharedConnection):
        def commit(self):
            raise RuntimeError('simulated commit failure')
    response = mobile_post(fuel_pg,[record()],CommitFailure)
    assert response.status_code == 503
    assert response.json['success'] is False
    assert row_counts(fuel_pg) == [0,0,0]


def test_year_factor_precedence_and_ambiguity(fuel_pg):
    cursor = fuel_pg.cursor()
    cursor.execute("INSERT INTO factores (categoria,unidad,factor,anio) VALUES ('Diésel fósil puro','Litros',2.7,2026)")
    assert resolver_factor_carbono(cursor,'diesel','litros',2026)[1] == 2.7
    assert resolver_factor_carbono(cursor,'diesel','litros',2025)[1] == 2.66155
    cursor.execute("INSERT INTO factores (categoria,unidad,factor,anio) VALUES ('Diésel fósil puro','Litros',2.8,2026)")
    with pytest.raises(ValueError,match='único'):
        resolver_factor_carbono(cursor,'diesel','litros',2026)


@pytest.mark.parametrize('amount',[-1,'nan','inf','abc'])
def test_fixed_invalid_input_is_rejected(fuel_pg,amount):
    import app as module
    client = module.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,empresa='AUDIT_A',es_admin=0)
    with patch.object(module,'get_db',return_value=SharedConnection(fuel_pg)):
        response = client.post('/api/combustible/fijo',data=record(cantidad=amount))
    assert response.status_code == 302
    assert row_counts(fuel_pg) == [0,0,0]


def test_dashboard_conciles_stored_emissions_with_fleet(fuel_pg):
    assert mobile_post(fuel_pg,[record(),record(periodo='2026-02')]).json['guardados'] == 2
    # El dashboard debe respetar la emisión histórica almacenada, no recalcularla.
    fuel_pg.cursor().execute('UPDATE registros SET emision=400 WHERE fecha=%s',('2026-01-01',))
    fuel_pg.commit()
    import app as module
    client = module.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,empresa='AUDIT_A',es_admin=0)
    contexts = []
    def capture(sender,template,context,**extra):
        contexts.append(context)
    for path,expected in [('/dashboard',666.155),('/dashboard?anio=2026',666.155),('/dashboard?anio=2025',0)]:
        with patch.object(module,'get_db',return_value=SharedConnection(fuel_pg)),template_rendered.connected_to(capture,module.app):
            response = client.get(path)
        assert response.status_code == 200
        context = contexts[-1]
        assert context['total_emision'] == pytest.approx(expected)
        assert sum(row['total'] for row in context['alcances_data']) == pytest.approx(expected,abs=0.01)
        assert context['total_emision_flota'] == pytest.approx(expected)
        if expected:
            assert '100.0%' in response.get_data(as_text=True)


@pytest.mark.parametrize('value',['nan','inf','-inf',-1,'abc'])
def test_reject_nonfinite_numbers(value):
    with pytest.raises(ValueError):
        numero_finito(value,'Cantidad')
    factor = resolver_factor('Combustión Fija','Diésel','litros')
    assert calcular_suministro(value,'litros',factor)['water_m3'] is None
    assert calcular_emision_residuo(value,420) is None


def test_period_is_normalized_to_first_day():
    assert fecha_periodo('2026-02').isoformat() == '2026-02-01'


def test_gas_volume_water_supply_uses_litre_to_m3_conversion():
    factor = resolver_factor('Combustión Fija','Gas Natural','m3')
    expected = calcular_suministro(1,'m3',factor)['water_m3']
    assert calcular_suministro(1000,'litros',factor)['water_m3'] == expected


def test_general_form_ignores_tampered_hidden_factor(fuel_pg):
    response = form_post(fuel_pg,'/registro',{
        'alcance_oculto':'Alcance 1','fuente':'Combustión Fija','fecha':'2026-01',
        'combustible':'Diésel fósil puro','unidad':'Litros','cantidad':'100','factor_oculto':'99999',
    })
    assert response.status_code == 302
    cursor = fuel_pg.cursor()
    cursor.execute('SELECT emision FROM registros')
    assert float(cursor.fetchone()[0]) == pytest.approx(266.155)


def test_general_batch_selects_factor_for_each_reporting_year(fuel_pg):
    fuel_pg.cursor().execute("INSERT INTO factores (categoria,unidad,factor,anio) VALUES ('Diésel fósil puro','Litros',2.7,2026)")
    fuel_pg.commit()
    response = form_post(fuel_pg,'/registro',{
        'alcance_oculto':'Alcance 1','modo_registro':'multiple','fuente':'Combustión Fija','area':'Test',
        'combustible':'Diésel fósil puro','unidad':'Litros','factor_oculto':'99999',
        'fecha_inicio':'2025-12','fecha_fin':'2026-01','batch_cantidad[]':['100','100'],
    })
    assert response.status_code == 302
    cursor = fuel_pg.cursor()
    cursor.execute('SELECT emision FROM registros ORDER BY fecha')
    assert [float(row[0]) for row in cursor.fetchall()] == pytest.approx([266.155,270])


@pytest.mark.parametrize('quantity',['-1','abc','nan','inf'])
def test_general_invalid_input_is_rejected(fuel_pg,quantity):
    form_post(fuel_pg,'/registro',{
        'alcance_oculto':'Alcance 1','fecha':'2026-01','cantidad':quantity,
        'fuente':'Combustión Fija','combustible':'Diésel fósil puro','unidad':'Litros',
    })
    assert row_counts(fuel_pg) == [0,0,0]


def test_electric_manual_and_imported_mwh_have_same_results(electric_pg):
    manual = form_post(electric_pg,'/registro',{
        'alcance_oculto':'Alcance 2','fuente':'Electricidad','fecha':'2026-01','cantidad':'1000',
        'origen_energia':'Convencional','sistema_elec':'SEN','nivel_tension':'media','tiene_irec':'No',
    })
    assert manual.status_code == 302
    imported = {'valid':True,'fecha':'2026-01-01','fuente':'Electricidad','alcance':'Alcance 1',
                'cantidad':1,'unidad':'MWh','categoria':'Red Eléctrica','actividad':'Test','area':'Test',
                'factor':99999,'emision':99999,'sistema':'SEN','nivel_tension':'media','origen_energia':'Convencional'}
    response = form_post(electric_pg,'/importar/confirmar',{'filas_json':json.dumps([imported])})
    assert response.location == '/importar/resultado'
    cursor = electric_pg.cursor()
    cursor.execute('SELECT cantidad,unidad,emision,emision_ubicacion,alcance FROM registros ORDER BY id')
    rows = cursor.fetchall()
    assert len(rows) == 2
    assert all(float(row[0]) == 1000 and row[1] == 'kWh' and float(row[2]) == 200 and float(row[3]) == 200 and row[4] == 'Alcance 2' for row in rows)


def test_electric_factor_does_not_use_future_year_or_missing_system(electric_pg):
    import app as module
    cursor = electric_pg.cursor()
    with pytest.raises(ValueError,match='factor eléctrico'):
        module._calcular_factores_electricos(cursor,'AUDIT_A','SEN',2025,12,'Convencional','No')
    with pytest.raises(ValueError,match='factor eléctrico'):
        module._calcular_factores_electricos(cursor,'AUDIT_A','SEA',2026,1,'Convencional','No')
    assert module._calcular_factores_electricos(cursor,'AUDIT_A','SEN',2026,1,'Convencional','No') == (0.2,0.2)


def test_irec_without_certificate_cannot_zero_emissions(electric_pg):
    response = form_post(electric_pg,'/registro',{
        'alcance_oculto':'Alcance 2','fuente':'Electricidad','fecha':'2026-01','cantidad':'1000',
        'origen_energia':'ERNC','sistema_elec':'SEN','nivel_tension':'media','tiene_irec':'Si',
    })
    assert response.location == '/registro'
    assert row_counts(electric_pg) == [0,0,0]


def test_irec_with_pdf_keeps_location_and_supply_emissions(electric_pg):
    electric_pg.cursor().execute('''CREATE TEMP TABLE irec_certificados (
        empresa TEXT, fecha_consumo TEXT, filename TEXT, contenido BYTEA, fecha_subida TEXT)''')
    electric_pg.commit()
    response = form_post(electric_pg,'/registro',{
        'alcance_oculto':'Alcance 2','fuente':'Electricidad','fecha':'2026-01','cantidad':'1000',
        'origen_energia':'ERNC','sistema_elec':'SEN','nivel_tension':'media','tiene_irec':'Si',
        'certificado_irec':(io.BytesIO(b'%PDF-1.4 synthetic test certificate'),'test.pdf'),
    })
    assert response.location == '/electricidad'
    cursor = electric_pg.cursor()
    cursor.execute('SELECT emision,emision_ubicacion FROM registros')
    row = cursor.fetchone()
    assert float(row[0]) == 0
    assert float(row[1]) == 200
    cursor.execute('SELECT huella_suministro_m3 FROM resultados_huella_suministro_agua')
    assert float(cursor.fetchone()[0]) > 0


def test_import_preview_keeps_electric_metadata_in_memory():
    import app as module
    import pandas as pd
    frame = pd.DataFrame([{'Mes':'Enero','Año':2026,'Level 1':'Electricidad','Level 2':'',
        'Sucursal':'Test','Tipo de Combustible':'Red Eléctrica','Cantidad':1,'Unidad de Medida':'MWh',
        'Sistema':'SEN','Nivel de Tensión':'media','Origen de Energía':'Convencional','IREC':'No'}])
    workbook = io.BytesIO()
    frame.to_excel(workbook, sheet_name='Datos Centralizados', index=False)
    workbook.seek(0)
    rows = module._parse_import_file(workbook)
    assert rows[0]['sistema'] == 'SEN'
    assert rows[0]['nivel_tension'] == 'media'
    assert rows[0]['unidad'] == 'MWh'


def test_electric_excel_upload_preview_and_confirmation(electric_pg):
    import app as module
    import pandas as pd
    workbook = io.BytesIO()
    pd.DataFrame([{'Mes':'Enero','Año':2026,'Level 1':'Electricidad',
        'Sucursal':'Test','Tipo de Combustible':'Red Eléctrica','Cantidad':1,'Unidad de Medida':'MWh',
        'Sistema':'SEN','Nivel de Tensión':'media','Origen de Energía':'Convencional','IREC':'No'}]).to_excel(workbook,sheet_name='Datos Centralizados',index=False)
    workbook.seek(0)
    client = module.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,empresa='AUDIT_A',es_admin=0)
    contexts = []
    def capture(sender,template,context,**extra):
        contexts.append(context)
    with patch.object(module,'get_db',return_value=SharedConnection(electric_pg)),template_rendered.connected_to(capture,module.app):
        response = client.post('/importar',data={'archivo':(workbook,'test.xlsx')})
    assert response.status_code == 200
    context = contexts[-1]
    assert context['validos'] == 1
    assert context['total_emision'] == 200
    with patch.object(module,'get_db',return_value=SharedConnection(electric_pg)):
        response = client.post('/importar/confirmar',data={'filas_json':context['filas_json']})
    assert response.location == '/importar/resultado'
    cursor = electric_pg.cursor()
    cursor.execute('SELECT cantidad,emision,emision_ubicacion FROM registros')
    row = cursor.fetchone()
    assert tuple(float(value) for value in row) == (1000,200,200)


def test_import_is_revalidated_and_whole_batch_rolls_back(fuel_pg):
    base = {'valid':True,'fecha':'2026-01-01','fuente':'Combustión Fija','alcance':'Alcance 1',
            'cantidad':100,'unidad':'litros','categoria':'Diésel fósil puro','actividad':'Test','area':'Test','factor':2.66155,'emision':99999}
    response = form_post(fuel_pg,'/importar/confirmar',{'filas_json':json.dumps([base,{**base,'cantidad':-1}])})
    assert response.location == '/importar'
    assert row_counts(fuel_pg) == [0,0,0]


def test_refrigerants_do_not_recalculate_only_the_displayed_rows(fuel_pg):
    cursor = fuel_pg.cursor()
    cursor.execute("INSERT INTO registros (empresa,fecha,fuente,categoria,unidad,cantidad,factor,emision) VALUES ('AUDIT_A','2026-01-01','Refrigerantes','R410A','kg',10,0,0)")
    cursor.execute("INSERT INTO factores (categoria,unidad,factor,anio) VALUES ('R410A','kg',2088,0)")
    fuel_pg.commit()
    import app as module
    client = module.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,empresa='AUDIT_A',es_admin=0)
    contexts = []
    def capture(sender,template,context,**extra):
        contexts.append(context)
    with patch.object(module,'get_db',return_value=SharedConnection(fuel_pg)),template_rendered.connected_to(capture,module.app):
        response = client.get('/refrigerantes')
    assert response.status_code == 200
    assert contexts[-1]['total_refrigerantes'] == 0
    assert contexts[-1]['registros'][0]['emision'] == 0


@pytest.mark.parametrize('quantity',['-1','nan','inf','abc'])
def test_waste_cannot_store_negative_or_nonfinite_quantity(fuel_pg,quantity):
    response = form_post(fuel_pg,'/formulario_residuos',{
        'tipo_ingreso':'manual','periodo[]':'2026-01','cantidad[]':quantity,
        'tipo_residuo[]':'Household residual waste','tratamiento[]':'Vertedero',
    })
    assert response.location == '/formulario_residuos'
    assert row_counts(fuel_pg) == [0,0,0]

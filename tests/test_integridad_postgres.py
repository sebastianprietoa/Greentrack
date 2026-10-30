"""Integración real en PostgreSQL local; tablas temporales, nunca datos reales."""
import os
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values
import psycopg2
from psycopg2.extensions import parse_dsn
import pytest

from services.migracion_integridad import reconciliar_integridad

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pg():
    dsn = os.getenv('TEST_DATABASE_URL') or dotenv_values(ROOT/'.env.staging').get('TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Requiere clon local de PostgreSQL (.env.staging)')
    fields = parse_dsn(dsn)
    if fields.get('host') not in ('127.0.0.1','localhost') or fields.get('dbname') != 'greentrack_staging':
        pytest.fail('Pruebas de escritura bloqueadas fuera del clon local')
    try:
        connection = psycopg2.connect(dsn, sslmode='disable', connect_timeout=5)
    except psycopg2.OperationalError:
        pytest.fail('No se pudo conectar al clon local; comprobar permisos de red y servidor.', pytrace=False)
    cursor = connection.cursor()
    # Definición mínima y real para detectar SQL inválido y comprobar transacciones/FKs.
    cursor.execute('CREATE TEMP TABLE registros (id INTEGER PRIMARY KEY, empresa TEXT, cantidad NUMERIC, emision NUMERIC)')
    cursor.execute('CREATE TEMP TABLE vehiculos (id INTEGER PRIMARY KEY, empresa TEXT)')
    cursor.execute('CREATE TEMP TABLE combustible_movil (id INTEGER PRIMARY KEY, empresa TEXT, vehiculo_id INTEGER, registro_id INTEGER)')
    cursor.execute('CREATE TEMP TABLE resultados_huella_suministro_agua (id INTEGER PRIMARY KEY, empresa TEXT NOT NULL, registro_id INTEGER UNIQUE, huella_suministro_m3 NUMERIC)')
    cursor.execute('''CREATE TEMP TABLE integridad_suministro_cuarentena (
        source_id INTEGER PRIMARY KEY, fila_original JSONB NOT NULL, motivo TEXT NOT NULL,
        fecha_archivo TIMESTAMP NOT NULL DEFAULT NOW())''')
    for company, master, mobile, supply, vehicle in [('AUDIT_A',101,201,301,401),('AUDIT_B',102,202,302,402)]:
        cursor.execute('INSERT INTO registros VALUES (%s,%s,100,268)', (master,company))
        cursor.execute('INSERT INTO vehiculos VALUES (%s,%s)', (vehicle,company))
        cursor.execute('INSERT INTO combustible_movil VALUES (%s,%s,%s,%s)', (mobile,company,vehicle,master))
        cursor.execute('INSERT INTO resultados_huella_suministro_agua VALUES (%s,%s,%s,1.35210516)', (supply,company,master))
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


class SharedConnection:
    """Permite que una ruta cierre su conexión sin destruir la sesión del test."""
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def close(self):
        pass


def run_delete(connection, path):
    import app as module
    module.app.config.update(TESTING=True)
    client = module.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,empresa='AUDIT_A',es_admin=0,rol='empresa')
    with patch.object(module, 'get_db', return_value=SharedConnection(connection)):
        response = client.post(path) if path.startswith('/vehiculos/eliminar/') else client.get(path)
    assert response.status_code == 302
    return client


def counts(connection, company):
    cursor = connection.cursor()
    result = []
    for table in ['registros','combustible_movil','resultados_huella_suministro_agua']:
        cursor.execute(f'SELECT COUNT(*) FROM {table} WHERE empresa=%s', (company,))
        result.append(cursor.fetchone()[0])
    return result


@pytest.mark.parametrize('path',[
    '/eliminar_cualquier_registro/manual/101',
    '/eliminar_cualquier_registro/vehiculo/201',
    '/combustible/movil/eliminar/201',
])
def test_all_delete_paths_remove_entire_consumption(pg, path):
    run_delete(pg,path)
    assert counts(pg,'AUDIT_A') == [0,0,0]
    assert counts(pg,'AUDIT_B') == [1,1,1]
    cursor = pg.cursor()
    cursor.execute("SELECT COALESCE(SUM(emision),0) FROM registros WHERE empresa='AUDIT_A'")
    assert cursor.fetchone()[0] == 0
    cursor.execute("SELECT COALESCE(SUM(huella_suministro_m3),0) FROM resultados_huella_suministro_agua WHERE empresa='AUDIT_A'")
    assert cursor.fetchone()[0] == 0


@pytest.mark.parametrize('path',[
    '/eliminar_cualquier_registro/manual/102',
    '/eliminar_cualquier_registro/vehiculo/202',
    '/combustible/movil/eliminar/202',
])
def test_other_company_cannot_be_deleted(pg,path):
    run_delete(pg,path)
    assert counts(pg,'AUDIT_A') == [1,1,1]
    assert counts(pg,'AUDIT_B') == [1,1,1]


def test_sql_error_rolls_back_the_entire_delete(pg):
    cursor = pg.cursor()
    cursor.execute("ALTER TABLE registros ADD CONSTRAINT prevent_delete_test CHECK (id > 0)")
    cursor.execute('''CREATE FUNCTION pg_temp.reject_delete() RETURNS trigger LANGUAGE plpgsql AS
        $$ BEGIN RAISE EXCEPTION 'simulated write failure'; END $$''')
    cursor.execute('CREATE TRIGGER reject_delete BEFORE DELETE ON registros FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_delete()')
    pg.commit()
    client = run_delete(pg,'/eliminar_cualquier_registro/manual/101')
    assert counts(pg,'AUDIT_A') == [1,1,1]
    with client.session_transaction() as session:
        assert any('No se pudo' in message for category,message in session.get('_flashes',[]))


def test_vehicle_deletion_preserves_consumption_history(pg):
    run_delete(pg,'/vehiculos/eliminar/401')
    assert counts(pg,'AUDIT_A') == [1,1,1]
    cursor = pg.cursor()
    cursor.execute('SELECT vehiculo_id FROM combustible_movil WHERE id=201')
    assert cursor.fetchone()[0] is None
    cursor.execute('SELECT COUNT(*) FROM vehiculos WHERE id=401')
    assert cursor.fetchone()[0] == 0


def test_vehicle_deletion_requires_post(pg):
    import app as module
    module.app.config.update(TESTING=True)
    client = module.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,empresa='AUDIT_A',es_admin=0,rol='empresa')
    response = client.get('/vehiculos/eliminar/401')
    assert response.status_code == 405
    cursor = pg.cursor()
    cursor.execute("SELECT COUNT(*) FROM vehiculos WHERE id=401")
    assert cursor.fetchone()[0] == 1


def test_vehicle_deletion_cannot_cross_company(pg):
    run_delete(pg,'/vehiculos/eliminar/402')
    cursor = pg.cursor()
    cursor.execute("SELECT COUNT(*) FROM vehiculos WHERE id=402 AND empresa='AUDIT_B'")
    assert cursor.fetchone()[0] == 1
    assert counts(pg,'AUDIT_A') == [1,1,1]
    assert counts(pg,'AUDIT_B') == [1,1,1]


@pytest.mark.parametrize('payload', [
    {},
    {'patente': '   '},
    {'patente': 'A' * 21},
    {'patente': 'TEST01', 'anio': '20XX'},
    {'patente': 'TEST01', 'anio': 2100},
])
def test_vehicle_api_rejects_invalid_payload_before_database(payload):
    import app as module
    module.app.config.update(TESTING=True)
    client = module.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,empresa='AUDIT_A',es_admin=0,rol='empresa')
    with patch.object(module, 'get_db', side_effect=AssertionError('No debe consultar la base de datos')):
        response = client.post('/api/vehiculos', json=payload)
    assert response.status_code == 400


def test_vehicle_api_rejects_non_json_payload_before_database():
    import app as module
    module.app.config.update(TESTING=True)
    client = module.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,empresa='AUDIT_A',es_admin=0,rol='empresa')
    with patch.object(module, 'get_db', side_effect=AssertionError('No debe consultar la base de datos')):
        response = client.post('/api/vehiculos', data='no-json', content_type='text/plain')
    assert response.status_code == 400


def test_missing_mobile_master_does_not_delete_another_company(pg):
    pg.cursor().execute('UPDATE combustible_movil SET registro_id=102 WHERE id=201')
    pg.commit()
    run_delete(pg,'/combustible/movil/eliminar/201')
    assert counts(pg,'AUDIT_B') == [1,1,1]
    assert counts(pg,'AUDIT_A') == [1,0,1]


def test_reconcile_is_recoverable_and_idempotent(pg):
    cursor = pg.cursor()
    cursor.execute("INSERT INTO resultados_huella_suministro_agua VALUES (303,'AUDIT_A',999,22.5)")
    first = reconciliar_integridad(pg)
    pg.commit()
    assert first['retirados_de_calculos'] == 1
    cursor.execute("SELECT fila_original->>'huella_suministro_m3' FROM integridad_suministro_cuarentena WHERE source_id=303")
    assert cursor.fetchone()[0] == '22.5'
    assert counts(pg,'AUDIT_A') == [1,1,1]
    assert reconciliar_integridad(pg)['retirados_de_calculos'] == 0
    pg.commit()
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cursor.execute("INSERT INTO resultados_huella_suministro_agua VALUES (304,'AUDIT_A',999,1)")
    pg.rollback()
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cursor.execute("INSERT INTO combustible_movil VALUES (204,'AUDIT_A',401,102)")
    pg.rollback()
    cursor.execute('DELETE FROM registros WHERE id=101')
    pg.commit()
    assert counts(pg,'AUDIT_A') == [0,0,0]

"""Unidades explícitas en reportes y precisión de los agregados de combustión."""
import io

from openpyxl import load_workbook
import pytest

from tests.test_aceptacion_roles import acceptance_pg, water_pg, fuel_pg, pg, database, client_for, context_get


@pytest.fixture
def unidades(acceptance_pg):
    connection,_=acceptance_pg
    cursor=connection.cursor()
    for source,scope,unit,quantity,factor,emission in [
        ('Residuos','Alcance 3','kg',11,6.41061,0.0705),
        ('Combustión Fija','Alcance 1','Litros',2,2.66155,5.3231),
        ('Electricidad','Alcance 2','kWh',100,0.3,30),
    ]:
        cursor.execute('''INSERT INTO registros (empresa,fecha,fuente,alcance,categoria,actividad,unidad,cantidad,factor,emision,emision_ubicacion)
            VALUES ('AUDIT_A','2025-01-01',%s,%s,'Prueba','Prueba',%s,%s,%s,%s,%s)''',
            (source,scope,unit,quantity,factor,emission,emission))
    connection.commit()
    return connection


def filas(book):
    records=[]
    for sheet in book:
        rows=list(sheet.values)
        if rows and 'Unidad del factor' in rows[0]:
            records.extend(dict(zip(rows[0],row)) for row in rows[1:] if any(v is not None for v in row))
    return records


@pytest.mark.parametrize('role,path',[
    ('empresa','/exportar_avanzado?anio=2025'),
    ('empresa','/exportar_ghg?anio=2025'),
    ('admin','/admin/exportar_ghg_global?anio=2025'),
    ('admin','/admin/exportar_todo?anio=2025'),
    ('admin','/admin/exportar/AUDIT_A?anio=2025'),
])
def test_excel_indica_base_del_factor_y_conserva_numeros(unidades,role,path):
    cursor=unidades.cursor()
    cursor.execute('SELECT id,cantidad,factor,emision FROM registros ORDER BY id')
    before=cursor.fetchall()
    with database(unidades):
        response=client_for(role).get(path)
    assert response.status_code==200
    book=load_workbook(io.BytesIO(response.data),read_only=True,data_only=True)
    rows=filas(book)
    assert rows
    for source,unit,quantity,factor,emission in [
        ('Residuos','kg CO₂e/t',11,6.41061,0.0705),
        ('Combustión Fija','kg CO₂e/Litros',2,2.66155,5.3231),
        ('Electricidad','kg CO₂e/kWh',100,0.3,30),
    ]:
        matches=[r for r in rows if r.get('Fuente',r.get('fuente'))==source]
        assert matches,source
        for r in matches:
            assert r['Unidad del factor']==unit
            assert r['Factor de emisión']==pytest.approx(factor)
            assert r.get('Cantidad',r.get('cantidad'))==quantity
            assert r.get('Emisión (kg CO₂e)',r.get('Emisiones (kg CO2)',r.get('emision')))==pytest.approx(emission)
    book.close()
    cursor.execute('SELECT id,cantidad,factor,emision FROM registros ORDER BY id')
    assert cursor.fetchall()==before


@pytest.mark.parametrize('sources',[
    ['Combustión Fija'],['Combustible Móvil'],['Combustión Fija','Combustible Móvil'],
])
def test_combustion_suma_valores_sin_redondear_cada_mes(acceptance_pg,sources):
    connection,_=acceptance_pg
    cursor=connection.cursor()
    for source in sources:
        for month in [1,2,3]:
            cursor.execute("INSERT INTO registros (empresa,fecha,fuente,emision) VALUES ('AUDIT_A',%s,%s,0.0044)",
                           (f'2025-{month:02d}-01',source))
    cursor.execute("INSERT INTO registros (empresa,fecha,fuente,emision) VALUES ('AUDIT_A','2026-01-01','Combustión Fija',100)")
    connection.commit()
    with database(connection):
        response,context=context_get(client_for('empresa'),'/combustion?anio=2025')
    expected=0.0132*len(sources)
    assert context['total_emision']==pytest.approx(expected,abs=1e-8)
    fixed=sum(context['tendencia_fija'])
    mobile=sum(context['tendencia_movil'])
    assert fixed==pytest.approx(0.0132 if 'Combustión Fija' in sources else 0,abs=1e-8)
    assert mobile==pytest.approx(0.0132 if 'Combustible Móvil' in sources else 0,abs=1e-8)
    assert fixed+mobile==pytest.approx(context['total_emision'],abs=1e-8)
    assert '0.01' in response.get_data(as_text=True)


def test_etiquetas_de_entrada_y_preview_residuos(unidades):
    with database(unidades):
        response,_=context_get(client_for('empresa'),'/formulario_residuos')
    html=response.get_data(as_text=True)
    assert 'CANTIDAD (kg) *' in html
    assert 'Factor (kg CO₂e/t)' in html
    assert '>Cantidad</th>' not in html
    assert '>Factor</th>' not in html
    assert 'Emisión (kg CO₂e)' in html
    assert 'Suma Total (Referencial, kg)' in html

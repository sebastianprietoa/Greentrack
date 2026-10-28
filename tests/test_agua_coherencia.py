"""Regresiones hídricas; toda escritura usa tablas temporales del clon local."""
from decimal import Decimal
from unittest.mock import patch

from flask import template_rendered
import pytest

from services.agua_inventario import crear_resumen_agua, diagnosticar, validar_nuevo_flujo
from services.huella_agua import resultados_por_periodo, construir_reporte_huella
from tests.test_integridad_postgres import pg, SharedConnection
from tests.test_combustibles_carbono import fuel_pg, form_post


def flujo(tipo, volumen, mes='2026-01', sede=101, mismo=True):
    return dict(sede_id=sede,periodo=mes+'-01',tipo_flujo=tipo,volumen_m3=volumen,
                retorna_mismo_sistema_hidrico=mismo,fuente_agua='Red pública',evidencia='Medición')


SEDE = dict(id=101,nombre_sede='Casa matriz',codigo_cuenca='C-01',nombre_cuenca='Cuenca',region='RM',comuna='Santiago')


def factor(id=1,valor='0.5',inicio='2026-01-01',fin='2026-01-31'):
    return dict(id=id,metodo='M',version_metodo='1',actividad='A',nivel_geografico='cuenca',
                codigo_geografico='C-01',periodo_inicio=inicio,periodo_fin=fin,
                factor_m3eq_m3=valor,fuente='Oficial',referencia='R',fecha_carga='2026-01-01',activo=True)


@pytest.mark.parametrize('entrada',['caudalimetro','boleta','no'])
@pytest.mark.parametrize('salida',['caudalimetro','descarga','no'])
def test_nueve_combinaciones_diagnostico(entrada,salida):
    expected = 'sin_medicion' if entrada=='no' else 'solo_entrada' if salida=='no' else 'ambos'
    assert diagnosticar(entrada,salida)==expected


def test_anual_suma_factores_aplicados_en_cada_mes():
    flujos = [flujo(t,v,m) for m in ['2026-01','2026-02'] for t,v in [('captacion',100),('retorno',20),('reuso',50)]]
    factores = [factor(),factor(2,'2','2026-02-01','2026-02-28')]
    mensual = resultados_por_periodo(flujos,[SEDE],factores,{2026:200})
    anual = resultados_por_periodo(flujos,[SEDE],factores,{2026:200},'anual')[0]
    assert [r['huella_escasez_m3eq'] for r in mensual]==[Decimal('40'),Decimal('160')]
    assert anual['huella_escasez_m3eq']==200
    assert anual['consumo_operativo_m3']==160  # El reúso no se descuenta dos veces.
    assert anual['intensidad_hidrica']==Decimal('0.8')
    assert anual['factor'] is None
    assert all(r['intensidad_hidrica'] is None for r in mensual)


@pytest.mark.parametrize('factores',[
    [factor(),factor(2)],
    [factor(inicio='2026-01-15')],
    [factor(fin='2026-01-15')],
    [factor(inicio='2026-02-01',fin='2026-02-28')],
])
def test_factor_ambiguo_o_vigencia_parcial_no_se_aplica(factores):
    rows = resultados_por_periodo([flujo('captacion',100),flujo('retorno',20)],[SEDE],factores)
    assert rows[0]['consumo_operativo_m3']==80
    assert rows[0]['huella_escasez_m3eq'] is None


@pytest.mark.parametrize('retorno,mismo,consumo',[(0,True,100),(20,False,100),(20,True,80),(20,None,None)])
def test_retorno_cero_externo_y_destino_desconocido(retorno,mismo,consumo):
    row = resultados_por_periodo([flujo('captacion',100),flujo('retorno',retorno,mismo=mismo)],[SEDE],[])[0]
    assert row['consumo_operativo_m3']==consumo


def test_balance_invalido_no_se_compensa_con_otro_mes():
    rows = resultados_por_periodo([flujo('captacion',100),flujo('retorno',150),
        flujo('captacion',100,'2026-02'),flujo('retorno',0,'2026-02')],[SEDE],[],vista='anual')
    assert rows[0]['consumo_operativo_m3'] is None
    assert rows[0]['error_balance']


@pytest.fixture
def water_pg(fuel_pg):
    cursor = fuel_pg.cursor()
    for table in ['configuracion','agua_sedes','agua_flujos','factores_escasez_agua','medidas_productivas','resultados_huella_agua']:
        cursor.execute(f'CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING DEFAULTS INCLUDING INDEXES)')
        cursor.execute(f'CREATE TEMP SEQUENCE test_{table}_seq START 2000')
        cursor.execute(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('pg_temp.test_{table}_seq')")
    cursor.execute("INSERT INTO agua_sedes (id,empresa,nombre_sede,activo) VALUES (101,'AUDIT_A','Casa matriz',true),(102,'AUDIT_B','Otra empresa',true)")
    fuel_pg.commit()
    return fuel_pg


def guardar_diagnostico(connection,entrada='caudalimetro',salida='caudalimetro',**extras):
    return form_post(connection,'/registro',dict(alcance_oculto='Agua',solo_diagnostico_agua='1',
        medicion_entrada_agua=entrada,medicion_salida_agua=salida,**extras))


def guardar(connection,tipo='captacion',volumen='100',**extras):
    return form_post(connection,'/registro',dict(alcance_oculto='Agua',tipo_flujo=tipo,volumen_m3=volumen,
        periodo='2026-01',retorna_mismo_sistema_hidrico='si',**extras))


def cantidad(connection):
    cursor = connection.cursor()
    cursor.execute('SELECT COUNT(*) FROM agua_flujos')
    return cursor.fetchone()[0]


def vista(connection,path):
    import app as module
    client = module.app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1,empresa='AUDIT_A',es_admin=0,rol='empresa')
    contexts=[]
    def capture(sender,template,context,**extra):
        contexts.append(context)
    with patch.object(module,'get_db',return_value=SharedConnection(connection)),template_rendered.connected_to(capture,module.app):
        response=client.get(path)
    return response,contexts[-1] if contexts else None


def test_diagnostico_persiste_y_no_confia_en_estado_del_cliente(water_pg):
    assert guardar_diagnostico(water_pg,'no','no',estado_medicion_agua='ambos').status_code==204
    assert guardar(water_pg,medicion_entrada_agua='caudalimetro',medicion_salida_agua='caudalimetro').status_code==302
    assert cantidad(water_pg)==0
    data=crear_resumen_agua(water_pg,'AUDIT_A')
    assert data['estado_medicion_agua']=='sin_medicion'
    assert data['huella_hidrica_total'] is None
    assert guardar_diagnostico(water_pg,'invalido','no').status_code==400
    assert guardar_diagnostico(water_pg,'boleta','no').status_code==204
    assert crear_resumen_agua(water_pg,'AUDIT_A')['estado_medicion_agua']=='solo_entrada'


def test_solo_entrada_permite_captacion_y_bloquea_retorno_reuso(water_pg):
    guardar_diagnostico(water_pg,'boleta','no')
    guardar(water_pg)
    for tipo in ['retorno','reuso']:
        guardar(water_pg,tipo,'20')
        response=form_post(water_pg,'/agua/retornos-reuso',dict(periodo='2026-01',tipo_flujo=tipo,volumen_m3='20'))
        assert response.status_code in (200,302)
    assert cantidad(water_pg)==1
    data=crear_resumen_agua(water_pg,'AUDIT_A')
    assert data['captacion_total']==100
    assert data['consumo'] is None
    assert data['tendencia']['2026-01']['consumo'] is None
    assert 'solo consumo de entrada' in data['mensaje_medicion_agua']


@pytest.mark.parametrize('valor',['-1','NaN','Infinity','-Infinity','abc'])
def test_volumen_no_valido_no_se_guarda(water_pg,valor):
    guardar_diagnostico(water_pg)
    guardar(water_pg,volumen=valor)
    assert cantidad(water_pg)==0


def test_balance_aislado_por_sede_y_mes(water_pg):
    guardar_diagnostico(water_pg)
    guardar(water_pg)
    cursor=water_pg.cursor()
    for sede,mes in [(101,'2026-02-01'),(None,'2026-01-01'),(102,'2026-01-01')]:
        with pytest.raises(ValueError):
            validar_nuevo_flujo(cursor,'AUDIT_A',sede,mes,'retorno',20)
        water_pg.rollback()
    guardar(water_pg,'retorno','101')
    assert cantidad(water_pg)==1
    guardar(water_pg,'retorno','20')
    assert cantidad(water_pg)==2
    assert crear_resumen_agua(water_pg,'AUDIT_A')['consumo']==80
    assert crear_resumen_agua(water_pg,'AUDIT_A')['tendencia']['2026-01']['consumo']==80


def test_lote_completo_se_revierte_si_un_mes_es_invalido(water_pg):
    guardar_diagnostico(water_pg)
    guardar(water_pg)
    response=form_post(water_pg,'/registro',dict(alcance_oculto='Agua',tipo_flujo='retorno',modo_registro='multiple',
        fecha_inicio='2026-01',fecha_fin='2026-02',**{'batch_cantidad[]':['20','20']}))
    assert response.status_code==302
    assert cantidad(water_pg)==1


def test_filtros_cobertura_y_diagnostico_en_resultados(water_pg):
    guardar_diagnostico(water_pg)
    guardar(water_pg)
    guardar(water_pg,'retorno','20')
    cursor=water_pg.cursor()
    cursor.execute("INSERT INTO medidas_productivas (empresa,anio,unidad,valor) VALUES ('AUDIT_A',2026,'Personas',10)")
    water_pg.commit()
    data=crear_resumen_agua(water_pg,'AUDIT_A','2026-01')
    assert data['consumo']==80
    assert data['periodos_inventario_completo']==1
    assert data['intensidad_hidrica'] is None
    assert crear_resumen_agua(water_pg,'AUDIT_A','2026')['intensidad_hidrica']==8
    assert crear_resumen_agua(water_pg,'AUDIT_A','2026-02')['consumo'] is None
    guardar_diagnostico(water_pg,'boleta','no')
    response,context=vista(water_pg,'/agua/resultados?periodo=2026-01')
    assert response.status_code==200
    assert all(r['consumo_operativo_m3'] is None for r in context['resultados'])
    response,_=vista(water_pg,'/agua?periodo=incorrecto')
    assert response.status_code==302


def test_exportacion_excel_respeta_diagnostico(water_pg):
    guardar_diagnostico(water_pg,'boleta','no')
    guardar(water_pg)
    response,_=vista(water_pg,'/agua/resultados/descargar?periodo=2026-01&vista=anual')
    assert response.status_code==200
    assert response.data.startswith(b'PK')
    reporte=construir_reporte_huella('AUDIT_A','2026',[flujo('captacion',100),flujo('retorno',20)],
        [SEDE],[factor()],vista='anual',diagnostico='solo_entrada',suministro_m3=2)
    resumen={r['Indicador']:r['Valor'] for r in reporte['Resumen']}
    assert resumen['Huella hídrica total [m³]']=='No disponible'
    assert reporte['Resultados por sede'][0]['Consumo operativo [m³]'] is None


def test_suministro_huerfano_excluido_y_resultado_faltante_bloquea_total(water_pg):
    guardar_diagnostico(water_pg)
    guardar(water_pg)
    guardar(water_pg,'retorno','20')
    cursor=water_pg.cursor()
    cursor.execute("INSERT INTO resultados_huella_suministro_agua (empresa,registro_id,huella_suministro_m3) VALUES ('AUDIT_A',999999,1000)")
    water_pg.commit()
    assert crear_resumen_agua(water_pg,'AUDIT_A')['huella_hidrica_total']==80
    cursor.execute("INSERT INTO registros (empresa,fecha,fuente,categoria,unidad,cantidad,factor,emision) VALUES ('AUDIT_A','2026-01-01','Combustión Estacionaria','Aceites usados','litros',10,0,0)")
    water_pg.commit()
    data=crear_resumen_agua(water_pg,'AUDIT_A')
    assert data['consumo']==80
    assert not data['suministro_completo']
    assert data['huella_hidrica_total'] is None
    response,_=vista(water_pg,'/agua')
    assert response.status_code==200
    assert '<canvas id="chartSuministro"' not in response.get_data(as_text=True)
    assert 'Faltan resultados de suministro' in response.get_data(as_text=True)


@pytest.mark.parametrize('entrada,salida',[('no','no'),('no','caudalimetro'),('boleta','no'),('caudalimetro','caudalimetro')])
def test_suministro_sin_resultado_guardado_se_calcula_independiente_del_diagnostico(water_pg,entrada,salida):
    guardar_diagnostico(water_pg,entrada,salida)
    cursor=water_pg.cursor()
    cursor.execute("""INSERT INTO registros (empresa,fecha,fuente,categoria,unidad,cantidad,factor,emision)
        VALUES ('AUDIT_A','2026-01-01','Electricidad','SEN','MWh',10,0,0),
        ('AUDIT_A','2026-01-01','Combustión Fija','Diésel','litros',1000,0,0)""")
    water_pg.commit()
    data=crear_resumen_agua(water_pg,'AUDIT_A','2026-01')
    assert data['huella_suministro_total']==pytest.approx(10.135227+1.35210516)
    assert data['suministro_completo'] and data['suministro_disponible']
    assert data['huella_hidrica_total'] is None
    assert 'huella azul' in data['mensaje_medicion_agua']
    response,_=vista(water_pg,'/agua?periodo=2026-01')
    html=response.get_data(as_text=True)
    assert '<canvas id="chartSuministro"' in html
    assert 'Total no disponible' in html
    cursor.execute('SELECT COUNT(*) FROM resultados_huella_suministro_agua')
    assert cursor.fetchone()[0]==0  # Consultar no escribe ni recalcula el histórico persistido.


def test_suministro_parcial_conserva_cero_valido_y_detalla_consumo_sin_factor(water_pg):
    guardar_diagnostico(water_pg,'no','no')
    cursor=water_pg.cursor()
    cursor.execute("""INSERT INTO registros (empresa,fecha,fuente,categoria,unidad,cantidad,factor,emision)
        VALUES ('AUDIT_A','2026-01-01','Electricidad','SEN','kWh',0,0,0),
        ('AUDIT_A','2026-01-01','Combustión Fija','Aceites usados','litros',10,0,0)""")
    water_pg.commit()
    data=crear_resumen_agua(water_pg,'AUDIT_A')
    assert data['huella_suministro_total']==0
    assert data['suministro_disponible'] and not data['suministro_completo']
    assert len(data['faltantes_suministro'])==1
    assert data['faltantes_suministro'][0]['categoria']=='Aceites usados'
    response,_=vista(water_pg,'/agua')
    html=response.get_data(as_text=True)
    assert '<canvas id="chartSuministro"' in html
    assert 'estimación mostrada es parcial' in html and 'Aceites usados' in html


def test_sin_energia_no_muestra_suministro_cero_como_estimacion(water_pg):
    guardar_diagnostico(water_pg,'no','no')
    data=crear_resumen_agua(water_pg,'AUDIT_A')
    assert not data['suministro_disponible'] and data['consumos_energeticos']==0
    response,_=vista(water_pg,'/agua')
    assert 'Sin consumos energéticos' in response.get_data(as_text=True)


def test_tendencia_no_muestra_huella_negativa_de_balance_invalido(water_pg):
    guardar_diagnostico(water_pg)
    guardar(water_pg)
    guardar(water_pg,'retorno','20')
    cursor=water_pg.cursor()
    cursor.execute("UPDATE agua_flujos SET volumen_m3=150 WHERE tipo_flujo='retorno'")
    water_pg.commit()
    data=crear_resumen_agua(water_pg,'AUDIT_A')
    assert data['consumo'] is None
    assert data['tendencia']['2026-01']['consumo'] is None
    assert data['errores_balance']


def test_eliminar_mes_actualiza_balance_y_preserva_otras_empresas(water_pg):
    guardar_diagnostico(water_pg)
    guardar(water_pg)
    guardar(water_pg,'retorno','20')
    cursor=water_pg.cursor()
    cursor.execute("INSERT INTO agua_flujos (empresa,sede_id,periodo,tipo_flujo,volumen_m3) VALUES ('AUDIT_B',102,'2026-01-01','captacion',50)")
    water_pg.commit()
    response=form_post(water_pg,'/agua/registros/eliminar',dict(sede_id='101',periodo='2026-01',vista='mensual'))
    assert response.status_code==302
    assert cantidad(water_pg)==1
    assert crear_resumen_agua(water_pg,'AUDIT_A')['consumo'] is None
    cursor.execute("SELECT volumen_m3 FROM agua_flujos WHERE empresa='AUDIT_B'")
    assert cursor.fetchone()[0]==50


def test_agrupa_dias_del_mismo_mes_al_validar_balance(water_pg):
    guardar_diagnostico(water_pg)
    guardar(water_pg)
    cursor=water_pg.cursor()
    cursor.execute("UPDATE agua_flujos SET periodo='2026-01-15'")
    water_pg.commit()
    guardar(water_pg,'retorno','20')
    assert cantidad(water_pg)==2
    assert crear_resumen_agua(water_pg,'AUDIT_A')['consumo']==80

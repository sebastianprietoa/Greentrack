from flask import Flask ,render_template ,request ,redirect ,flash ,session ,send_file ,jsonify ,url_for 
from dotenv import load_dotenv

load_dotenv()

import psycopg2 
import psycopg2 .extras 
from datetime import datetime 
import pandas as pd 
import hashlib 
import os 
import io 
import json 
import math
import re 
import tempfile 
from decimal import Decimal 
from extractor_sinader import extract_sinader_data 
from extractor_sidrep import extract_sidrep_data ,clasificar_defra 
from auth_utils import hash_password ,verify_password ,verify_api_token 
from routes import register_blueprints 
from services .huella_agua import (
HuellaAguaError ,
buscar_factor_escasez_mas_especifico ,
calcular_captacion_total ,
calcular_consumo_operativo_estimado ,
calcular_intensidad_hidrica_total ,
calcular_huella_escasez ,
calcular_retornos_mismo_sistema ,
calcular_retornos_totales ,
calcular_reuso_interno ,
consolidar_resultado_sede ,
construir_reporte_huella ,
clasificar_resultado_huella ,
generar_indicador_calidad_datos ,
validar_factor_escasez ,
validar_resultado_no_duplicado ,
)
from services.residuos_factores import (
    calcular_emision_residuo,
    normalizar_tratamiento_residuo,
    resolver_factor_residuo,
)
from services.energia_factores import cargar_registro, calcular_suministro, resolver_factor
from services.registros_integridad import eliminar_consumo, eliminar_consumo_movil
from services.combustibles_carbono import fecha_periodo, numero_finito, resolver_factor_carbono, resolver_factor_catalogo, unidad_canonica
from services.agua_inventario import crear_resumen_agua, diagnosticar, leer_diagnostico, MENSAJES, normalizar_periodo, validar_nuevo_flujo
from services.huella_agua import consolidar_grupo_seguro, resultados_por_periodo

app =Flask (__name__ )
app .secret_key =os .getenv ("SECRET_KEY","clave_segura_provisional")
app .config ["TEMPLATES_AUTO_RELOAD"]=True 
register_blueprints (app )

ROLE_ADMIN = "admin"
ROLE_MANAGER = "gestor_empresa"
ROLE_COMPANY = "empresa"
DEMO_COMPANY = "Demo GreenTrack"
DEMO_EMAIL = os.getenv("DEMO_USER_EMAIL", "demo@greenticket.cl")


def current_role():
    # ``es_admin`` es el indicador histórico y autoritativo para administradores.
    # Algunas cuentas antiguas conservan un valor de ``rol`` desactualizado, por
    # lo que ese campo no debe quitarles acceso a la gestión de empresas.
    if session.get("es_admin") == 1:
        return ROLE_ADMIN
    return session.get("rol") or ROLE_COMPANY


def is_admin():
    return current_role() == ROLE_ADMIN


def can_manage_companies():
    return "user_id" in session and current_role() in (ROLE_ADMIN, ROLE_MANAGER)

@app .context_processor 
def inject_pendientes_count ():
    if session .get ('es_admin')==1 :
        try :
            conn =get_db ()
            cur =conn .cursor ()
            cur .execute ("SELECT COUNT(*) FROM pending_pdf_uploads WHERE estado = 'pendiente'")
            total =cur .fetchone ()[0 ]or 0 
            cur .execute ("SELECT COUNT(*) FROM tickets WHERE estado = 'Abierto'")
            tickets_abiertos =cur .fetchone ()[0 ]or 0 
            conn .close ()
            return {'total_pendientes':total ,'tickets_abiertos':tickets_abiertos }
        except Exception :
            return {'total_pendientes':0 ,'tickets_abiertos':0 }
    if session .get ('user_id')and session .get ('es_admin')==0 :
        try :
            conn =get_db ()
            cur =conn .cursor ()
            cur .execute ("SELECT COUNT(*) FROM tickets WHERE empresa = %s AND estado != 'Cerrado'",(session .get ('empresa'),))
            mis_tickets =cur .fetchone ()[0 ]or 0 
            conn .close ()
            return {'mis_tickets_abiertos':mis_tickets }
        except Exception :
            return {'mis_tickets_abiertos':0 }
    return {}

    # Configuración de Base de Datos para Producción (Railway) o Local
DATABASE_URL =os .getenv ("DATABASE_URL")

def get_db ():
# 1. Obtenemos la variable secreta de Railway
    db_url =os .getenv ("DATABASE_URL")

    # 2. Si Railway no está mandando la variable, esto evitará que el sistema colapse en silencio
    if not db_url :
        raise ValueError ("¡Error CRÍTICO: No se encontró la variable DATABASE_URL en Railway!")

        # 3. Corrección automática: Railway a veces entrega la URL como "postgres://", 
        # pero Python exige que diga "postgresql://". Esto lo arregla solo.
    if db_url .startswith ("postgres://"):
        db_url =db_url .replace ("postgres://","postgresql://",1 )

        # 4. Conectamos forzando el modo seguro (SSL), que Railway a veces exige
    conn = psycopg2 .connect (db_url ,sslmode =os.getenv('PGSSLMODE', 'require'))
    try:
        _ensure_productive_period_schema(conn)
    except Exception:
        conn.rollback()
        conn.close()
        raise
    return conn


def _ensure_productive_period_schema(conn):
    """Compatibiliza bases existentes sin eliminar ni reemplazar registros."""
    cursor = conn.cursor()
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext('greentrack:medidas_productivas:periodo'))")
    cursor.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'medidas_productivas'"
    )
    if cursor.fetchone() is None:
        conn.rollback()
        return
    cursor.execute(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'medidas_productivas' "
        "AND column_name = 'periodo'"
    )
    column = cursor.fetchone()
    if column is None:
        cursor.execute("ALTER TABLE medidas_productivas ADD COLUMN periodo TEXT")

    cursor.execute(
        "UPDATE medidas_productivas SET periodo = anio::text "
        "WHERE periodo IS NULL OR periodo = ''"
    )
    cursor.execute("ALTER TABLE medidas_productivas ALTER COLUMN periodo SET NOT NULL")
    cursor.execute(
        "ALTER TABLE medidas_productivas DROP CONSTRAINT IF EXISTS medidas_productivas_empresa_anio_key"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS medidas_productivas_empresa_periodo_key "
        "ON medidas_productivas (empresa, periodo)"
    )
    conn.commit()
    # Filtro para números con puntos y comas
@app .template_filter ('formato_cl')
def formato_cl (value ):
    try :
        return "{:,.2f}".format (float (value )).replace (',','X').replace ('.',',').replace ('X','.')
    except :
        return value 

@app .template_filter ('formato_entero_cl')
def formato_entero_cl (value ):
    try :
        return "{:,.0f}".format (float (value )).replace (',','X').replace ('.',',').replace ('X','.')
    except :
        return value 

        # Filtro compacto para KPIs: muestra K o M cuando el número es grande
@app .template_filter ('formato_kpi')
def formato_kpi (value ):
    try :
        v =float (value )
        if v >=1_000_000 :
            return "{:,.2f} M".format (v /1_000_000 ).replace (',','X').replace ('.',',').replace ('X','.')
        elif v >=10_000 :
            return "{:,.1f} K".format (v /1_000 ).replace (',','X').replace ('.',',').replace ('X','.')
        else :
            return "{:,.2f}".format (v ).replace (',','X').replace ('.',',').replace ('X','.')
    except :
        return value 

        # Filtro para mostrar Mes y Año
@app .template_filter ('mes_anio')
def mes_anio (fecha_str ):
    try :
        meses =["","Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
        fecha_obj =datetime .strptime (fecha_str [:10 ],"%Y-%m-%d")
        return f"{meses [fecha_obj .month ]} {fecha_obj .year }"
    except :
        return fecha_str 

        # Factores de emisión base
FACTORES ={
"Electricidad":{"kWh":0.233 },
"Transporte":{"km":0.192 },
"Gas":{"m3":2.0 },
"Combustible móvil":{"L":2.68 },
"Residuos":{"kg":0.45 },
"Agua":{"m3":0.34 }
}

UNIDADES_PRODUCTIVAS =[
"Toneladas producto",
"Toneladas producidas",
"Kilogramos producidos",
"Personas",
"Horas de trabajo",
"% de ocupación",
"Noches ocupadas",
"Habitaciones vendidas",
"Pasajeros transportados",
"Kilómetros recorridos",
"Servicios prestados",
"Órdenes procesadas",
"Visitas",
"Atenciones",
"M2 construidos",
"M2 operados",
"Litros embotellados",
"Toneladas transportadas",
"Tickets vendidos",
]

SECTORES_EMPRESA ={
"Turismo":["Transporte","Alojamiento","Alimentación","Agencias de viaje","Eventos","Empresas de transporte"],
"Servicios":["Consultoría","Tecnología","Outsourcing","Backoffice","Call center","Gestión administrativa"],
"Salud":["Clínicas","Hospitales","Centros médicos","Laboratorios","Odontología","Telemedicina"],
"Comercio":["Retail","Mayoristas","E-commerce","Distribución","Importadora","Exportadora"],
"Construcción":["Constructoras","Inmobiliarias","Obras civiles","Servicios de mantenimiento","Contratistas"],
"Industria y manufactura":["Alimentos y bebidas","Metalmecánica","Textil","Plásticos","Química","Celulosa y papel"],
"Agroindustria":["Fruticultura","Lechería","Ganadería","Vitivinícola","Forestal","Procesamiento agrícola"],
"Transporte y logística":["Carga terrestre","Logística","Courier","Transporte marítimo","Transporte aéreo","Última milla"],
"Energía y utilities":["Generación eléctrica","Distribución eléctrica","Gas","Agua y saneamiento","Energías renovables"],
"Educación":["Colegios","Universidades","Institutos","Capacitación","Educación técnica"],
"Sector público":["Municipalidades","Servicios públicos","Hospitales públicos","Universidades estatales","Gobierno regional"],
"Minería":["Extracción","Servicios mineros","Proveedores mineros","Exploración","Procesamiento"],
"Finanzas":["Bancos","Aseguradoras","Fintech","Corredoras","Servicios financieros"],
}

def calcular_intensidad_emisiones (conn ,empresa ,periodo ,valor_medida ):
    try :
        valor =float (valor_medida )
    except Exception :
        valor =0.0 
    if valor <=0 :
        return 0.0 ,None 
    cur =conn .cursor ()
    periodo = str(periodo or '')
    filtro = "TO_CHAR(fecha::date, 'YYYY-MM') = %s" if len(periodo) == 7 else "EXTRACT(YEAR FROM fecha::date)::text = %s"
    cur .execute (
    f"SELECT COALESCE(SUM(emision), 0) FROM registros WHERE empresa = %s AND {filtro}",
    (empresa ,periodo)
    )
    total_kg =float (cur .fetchone ()[0 ]or 0 )
    total_t =round (total_kg /1000.0 ,4 )
    intensidad =round (total_kg /valor ,6 )
    return total_t ,intensidad 

def obtener_medidas_productivas (conn ,empresa ):
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("""
        SELECT anio, periodo, unidad, valor, fecha_actualizacion
        FROM medidas_productivas
        WHERE empresa = %s
        ORDER BY anio DESC, id DESC
    """,(empresa ,))
    medidas =[dict (row )for row in cursor .fetchall ()]
    for medida in medidas :
        total_t ,intensidad =calcular_intensidad_emisiones (conn ,empresa ,medida.get('periodo') or medida['anio'],medida ['valor'])
        medida ['emisiones_anio_tco2e']=total_t 
        medida ['intensidad_emisiones']=intensidad 
    return medidas 

def obtener_medidas_empresas (conn ):
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("""
        SELECT empresa, anio, periodo, unidad, valor, fecha_actualizacion
        FROM medidas_productivas
        ORDER BY empresa, anio DESC, id DESC
    """)
    medidas ={}
    for row in cursor .fetchall ():
        emp =row ['empresa']
        if emp and emp not in medidas :
            medidas [emp ]=dict (row )
    for emp ,medida in medidas .items ():
        total_t ,intensidad =calcular_intensidad_emisiones (conn ,emp ,medida.get('periodo') or medida['anio'],medida ['valor'])
        medida ['emisiones_anio_tco2e']=total_t 
        medida ['intensidad_emisiones']=intensidad 
    return medidas 

def guardar_medida_productiva (cursor ,empresa ,periodo ,unidad ,valor ):
    if not unidad or not str(unidad).strip():
        raise ValueError('Selecciona una unidad para la medida productiva.')
    periodo = str(periodo or '').strip()
    if not re.fullmatch(r'\d{4}(?:-\d{2})?', periodo):
        raise ValueError('El período de la medida productiva no es válido.')
    anio = int(periodo[:4])
    if not 1 <= anio <= 9999 or (len(periodo) == 7 and not 1 <= int(periodo[5:]) <= 12):
        raise ValueError('El período de la medida productiva no es válido.')
    valor=numero_finito(valor,'La medida productiva',positivo=True)
    cursor .execute ("""
        INSERT INTO medidas_productivas (empresa, anio, periodo, unidad, valor, fecha_actualizacion)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (empresa, periodo) DO UPDATE SET
            unidad = EXCLUDED.unidad,
            valor = EXCLUDED.valor,
            fecha_actualizacion = EXCLUDED.fecha_actualizacion
    """,(empresa ,anio ,periodo ,unidad ,float (valor ),datetime .now ().strftime ("%Y-%m-%d %H:%M")))

def _normalizar_texto_suministro (valor ):
    return (str (valor or '').strip ().lower ()
    .replace ('é','e').replace ('é','e')
    .replace ('ó','o').replace ('ó','o')
    .replace ('í','i').replace ('í','i')
    .replace ('á','a').replace ('á','a')
    .replace ('ú','u').replace ('ú','u'))

def _seed_factores_huella_suministro_agua (cursor ):
    registry = cargar_registro()
    version = registry.get("schema_version", "1.0.0")
    for factor in registry.get("water_factor_sources", []):
        factor_id = factor.get("factor_id")
        if not factor_id:
            continue
        tipo = "electricidad" if factor_id.startswith("water_electricity") else "combustible"
        cursor.execute("""
            INSERT INTO factores_huella_suministro_agua
            (tipo_energia, categoria_factor, factor_m3_gj, fuente, referencia, version, activo,
             factor_code, factor_value, factor_unit, source_unit, dataset, geography, method,
             quality, source_id, water_status)
            VALUES (%s, %s, NULL, %s, %s, %s, TRUE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tipo_energia, categoria_factor, version)
            DO UPDATE SET factor_code = EXCLUDED.factor_code,
                          factor_value = EXCLUDED.factor_value,
                          factor_unit = EXCLUDED.factor_unit,
                          source_unit = EXCLUDED.source_unit,
                          dataset = EXCLUDED.dataset,
                          geography = EXCLUDED.geography,
                          method = EXCLUDED.method,
                          quality = EXCLUDED.quality,
                          source_id = EXCLUDED.source_id,
                          water_status = EXCLUDED.water_status,
                          activo = TRUE
        """, (
            tipo, factor_id, factor.get("source_id") or factor.get("dataset") or "Codex v1", factor.get("notes"), version,
            factor_id, factor.get("water_factor"), factor.get("unit") or ("m3/" + str(factor.get("source_unit"))) if factor.get("source_unit") else None, factor.get("source_unit"),
            factor.get("dataset"), factor.get("geo"), factor.get("method"),
            factor.get("quality"), factor.get("source_id"), "ready",
        ))

def _categoria_suministro_desde_registro (fuente ,categoria ,origen_energia =None ,sistema =None ):
    texto =" ".join ([
    _normalizar_texto_suministro (fuente ),
    _normalizar_texto_suministro (categoria ),
    _normalizar_texto_suministro (origen_energia ),
    _normalizar_texto_suministro (sistema ),
    ])
    if 'electricidad'in texto :
        if 'hidro'in texto :
            return 'electricidad','hydropower'
        if 'eolic'in texto or 'wind'in texto :
            return 'electricidad','wind'
        if 'solar'in texto :
            return 'electricidad','solar_thermal'
        if 'nuclear'in texto or 'uranio'in texto :
            return 'electricidad','nuclear'
        if 'gas'in texto :
            return 'electricidad','natural_gas'
        if 'carbon'in texto or 'carbón'in texto :
            return 'electricidad','coal'
        return 'electricidad',None
    if any (x in texto for x in ['combustion','combusti','combustible']):
        if 'gas natural'in texto :
            return 'combustible','natural_gas'
        if 'carbon'in texto or 'carbón'in texto :
            return 'combustible','coal'
        return 'combustible','crude_oil_proxy'
    return None ,None

def _es_fuente_combustible_suministro (fuente ):
    texto =_normalizar_texto_suministro (fuente )
    return 'combusti'in texto or 'combustible'in texto

def _clasificar_factor_catalogo (categoria ,unidad =None ,nombre_chile =None ):
    texto =" ".join ([
    _normalizar_texto_suministro (categoria ),
    _normalizar_texto_suministro (nombre_chile ),
    _normalizar_texto_suministro (unidad ),
    ])
    if any (kw in texto for kw in ('refrigerante','freon','freon','hfc','cfc','r-','r4')):
        return 'refrigerantes'
    if any (kw in texto for kw in ('residuo','residuos','waste','recicl','vertedero','compost','inciner','tratamiento','relleno sanitario')):
        return 'residuos'
    if any (kw in texto for kw in (
        'diesel','diessel','bencina','gasolina','glp','gas licuado','gas lp','gas natural',
        'gas natural comprimido','gas natural licuado','gnc','gnl','petroleo','petróleo',
        'fuel oil','aceite','kerosene','querosene','parafina','nafta','gasoil','biodiesel',
        'etanol','bioetanol','biogas','biogás','carbon','carbón'
    )):
        return 'combustibles'
    return 'otros'

def _energia_gj_desde_consumo (cantidad ,unidad ,categoria_factor ,categoria_consumo ):
    try :
        cantidad_dec =Decimal (str (cantidad or 0 ))
    except Exception :
        return None 
    if cantidad_dec <0 :
        return None 
    unidad_norm =_normalizar_texto_suministro (unidad )
    categoria_norm =_normalizar_texto_suministro (categoria_consumo )
    if unidad_norm in ('gj','gigajoule','gigajoules'):
        return cantidad_dec 
    if unidad_norm in ('mj','megajoule','megajoules'):
        return cantidad_dec *Decimal ('0.001')
    if unidad_norm in ('kwh','kw h','kilowatt hour','kilowatt-hour'):
        return cantidad_dec *Decimal ('0.0036')
    if unidad_norm in ('mwh',):
        return cantidad_dec *Decimal ('3.6')
    if unidad_norm in ('l','lt','lts','litro','litros','liter','liters'):
        if 'glp'in categoria_norm or 'licuado'in categoria_norm :
            return cantidad_dec *Decimal ('0.0253')
        if 'bencina'in categoria_norm or 'gasolina'in categoria_norm :
            return cantidad_dec *Decimal ('0.0342')
        if 'kerosene'in categoria_norm or 'parafina'in categoria_norm :
            return cantidad_dec *Decimal ('0.0350')
        return cantidad_dec *Decimal ('0.0386')
    if unidad_norm in ('m3','m³','m³','metro cubico','metros cubicos'):
        if categoria_factor =='natural_gas'or 'gas natural'in categoria_norm :
            return cantidad_dec *Decimal ('0.0380')
    if unidad_norm in ('kg','kilogramo','kilogramos'):
        if categoria_factor =='coal':
            return cantidad_dec *Decimal ('0.0240')
        if categoria_factor =='natural_gas':
            return cantidad_dec *Decimal ('0.0500')
        return cantidad_dec *Decimal ('0.0430')
    return None 

def calcular_huella_suministro_registro(cursor, registro_id, tension_default="media"):
    cursor.execute("""
        SELECT id, fecha, empresa, fuente, categoria, unidad, cantidad,
               origen_energia, sistema, nivel_tension
        FROM registros
        WHERE id = %s
    """, (registro_id,))
    registro = cursor.fetchone()
    if not registro:
        return None

    tension = registro[9] or tension_default
    factor = resolver_factor(
        registro[3], registro[4], registro[5], registro[7], registro[8], tension
    )
    calculo = calcular_suministro(registro[6], registro[5], factor)
    factor_id = None
    if factor.get("water_factor_id"):
        cursor.execute("""
            SELECT id
            FROM factores_huella_suministro_agua
            WHERE factor_code = %s AND activo = TRUE
            ORDER BY fecha_carga DESC, id DESC
            LIMIT 1
        """, (factor["water_factor_id"],))
        factor_row = cursor.fetchone()
        factor_id = factor_row[0] if factor_row else None

    quality = factor.get("quality") or "N/A"
    status = factor.get("water_status") or "unknown"
    warning = calculo.get("warning")
    if status not in {"ready", "ready_proxy_geo", "ready_mass_only", "ready_if_naphtha"}:
        quality = status
    observation = warning or "Factor Freshwater de suministro upstream aplicado."
    cursor.execute("""
        INSERT INTO resultados_huella_suministro_agua
        (registro_id, empresa, fecha, fuente_consumo, categoria_consumo, unidad_consumo,
         cantidad_consumo, energia_gj, factor_id, factor_m3_gj, huella_suministro_m3,
         calidad_resultado, observaciones, fecha_calculo, energy_id, water_factor_id,
         source_id, dataset, geography, method, water_quality, warning, source_unit,
         normalized_quantity, water_status, nivel_tension)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, NULL, %s, %s, %s, NOW(),
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (registro_id) DO UPDATE SET
            empresa = EXCLUDED.empresa,
            fecha = EXCLUDED.fecha,
            fuente_consumo = EXCLUDED.fuente_consumo,
            categoria_consumo = EXCLUDED.categoria_consumo,
            unidad_consumo = EXCLUDED.unidad_consumo,
            cantidad_consumo = EXCLUDED.cantidad_consumo,
            factor_id = EXCLUDED.factor_id,
            huella_suministro_m3 = EXCLUDED.huella_suministro_m3,
            calidad_resultado = EXCLUDED.calidad_resultado,
            observaciones = EXCLUDED.observaciones,
            fecha_calculo = NOW(),
            energy_id = EXCLUDED.energy_id,
            water_factor_id = EXCLUDED.water_factor_id,
            source_id = EXCLUDED.source_id,
            dataset = EXCLUDED.dataset,
            geography = EXCLUDED.geography,
            method = EXCLUDED.method,
            water_quality = EXCLUDED.water_quality,
            warning = EXCLUDED.warning,
            source_unit = EXCLUDED.source_unit,
            normalized_quantity = EXCLUDED.normalized_quantity,
            water_status = EXCLUDED.water_status,
            nivel_tension = EXCLUDED.nivel_tension
    """, (
        registro[0], registro[2], registro[1], registro[3], registro[4], registro[5],
        registro[6], factor_id, calculo.get("water_m3"), quality, observation,
        factor.get("energy_id"), factor.get("water_factor_id"), factor.get("source_id"),
        factor.get("dataset"), factor.get("geography"), factor.get("method"),
        factor.get("quality"), warning, factor.get("source_unit"),
        calculo.get("normalized_quantity"), status, tension,
    ))
    return {
        "registro_id": registro[0],
        "energy_id": factor.get("energy_id"),
        "water_status": status,
        "quality": quality,
        "warning": warning,
        "huella_suministro_m3": calculo.get("water_m3"),
    }

def recalcular_huella_suministro_historica(conn, empresa=None, forzar=True):
    cursor =conn .cursor ()
    params =[]
    filtros =[
    """
        (
            LOWER(COALESCE(r.alcance, '')) = 'alcance 2'
            OR LOWER(COALESCE(r.fuente, '')) LIKE %s
            OR LOWER(COALESCE(r.fuente, '')) LIKE %s
            OR LOWER(COALESCE(r.fuente, '')) LIKE %s
        )
    """
    ]
    params .extend (["%electricidad%","%combusti%","%combustible%"])
    if empresa :
        filtros .append ("r.empresa = %s")
        params .append (empresa )

    join_filter = "" if forzar else "LEFT JOIN resultados_huella_suministro_agua rs ON rs.registro_id = r.id"
    missing_filter = "" if forzar else "AND rs.registro_id IS NULL"
    cursor.execute(
        f"""
            SELECT DISTINCT r.id
            FROM registros r
            {join_filter}
            WHERE {" AND ".join(filtros)} {missing_filter}
            ORDER BY r.fecha ASC, r.id ASC
        """,
        params,
    )
    registros_pendientes =[row [0 ]for row in cursor .fetchall ()]

    procesados =0
    omitidos =0
    for registro_id in registros_pendientes :
        try :
            cursor .execute ("SAVEPOINT sp_huella_suministro_historica")
            calcular_huella_suministro_registro (cursor ,registro_id )
            cursor .execute ("RELEASE SAVEPOINT sp_huella_suministro_historica")
            procesados +=1
        except Exception :
            cursor .execute ("ROLLBACK TO SAVEPOINT sp_huella_suministro_historica")
            omitidos +=1
    conn .commit ()
    return {
    "pendientes":len (registros_pendientes ),
    "procesados":procesados ,
    "omitidos":omitidos ,
    }

def _seed_admin_account(cursor):
    initial_password = os.getenv("ADMIN_INITIAL_PASSWORD")
    if not initial_password:
        return
    cursor.execute("""
        INSERT INTO usuarios
        (empresa, email, password, contacto, es_admin, rol, es_demo, fecha_registro)
        VALUES (%s, %s, %s, %s, 1, 'admin', FALSE, %s)
        ON CONFLICT (email) DO NOTHING
    """, ("Administrador", "admin@huella.com", hash_password(initial_password),
          "Administrador", datetime.now().strftime("%Y-%m-%d %H:%M")))


def _seed_demo_account(cursor):
    demo_password = os.getenv("DEMO_USER_PASSWORD")
    if not demo_password:
        return

    password_hash = hash_password(demo_password)
    cursor.execute("SELECT id FROM usuarios WHERE email = %s OR empresa = %s LIMIT 1", (DEMO_EMAIL, DEMO_COMPANY))
    existing = cursor.fetchone()
    if existing:
        cursor.execute("""
            UPDATE usuarios
            SET empresa = %s, email = %s, password = %s, contacto = %s,
                sector_empresa = %s, tipo_empresa = %s, es_admin = 0,
                rol = %s, es_demo = TRUE
            WHERE id = %s
        """, (DEMO_COMPANY, DEMO_EMAIL, password_hash, "Cuenta demostrativa",
               "Servicios", "Empresa de prueba", ROLE_MANAGER, existing[0]))
    else:
        cursor.execute("""
            INSERT INTO usuarios
            (empresa, email, password, contacto, sector_empresa, tipo_empresa,
             es_admin, rol, es_demo, fecha_registro)
            VALUES (%s, %s, %s, %s, %s, %s, 0, %s, TRUE, %s)
        """, (DEMO_COMPANY, DEMO_EMAIL, password_hash, "Cuenta demostrativa",
               "Servicios", "Empresa de prueba", ROLE_MANAGER,
               datetime.now().strftime("%Y-%m-%d %H:%M")))

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS greentrack_demo_seed (
            id INTEGER PRIMARY KEY,
            version TEXT NOT NULL,
            seeded_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cursor.execute("SELECT 1 FROM greentrack_demo_seed WHERE id = 1")
    if cursor.fetchone():
        cursor.execute("""
            SELECT id FROM registros
            WHERE empresa = %s AND (LOWER(COALESCE(fuente, '')) LIKE '%%electricidad%%'
                OR LOWER(COALESCE(fuente, '')) LIKE '%%combusti%%'
                OR LOWER(COALESCE(fuente, '')) LIKE '%%combustible%%'
                OR LOWER(COALESCE(alcance, '')) = 'alcance 2')
        """, (DEMO_COMPANY,))
        for (registro_id,) in cursor.fetchall():
            calcular_huella_suministro_registro(cursor, registro_id)
        return

    demo_records = [
        ("2025-01-15", "Operaciones", "Alcance 1", "Combustión Fija", "Gas natural", "Caldera central", "m3", 1200, 2.0, 2400),
        ("2025-02-10", "Logística", "Alcance 1", "Combustible Móvil", "Diésel", "Flota corporativa", "litros", 800, 2.68, 2144),
        ("2025-03-01", "Operaciones", "Alcance 2", "Electricidad", "Electricidad SEN", "Consumo de red eléctrica", "kWh", 5000, 0.25, 1250),
        ("2025-04-10", "Mantenimiento", "Alcance 1", "Refrigerantes", "R-410A", "Recarga de equipo", "kg", 12, 2088, 25056),
        ("2025-05-10", "Operaciones", "Alcance 3", "Residuos", "Residuos generales", "Disposición de residuos", "tonne", 12, 0.5, 6),
    ]
    for record in demo_records:
        cursor.execute("""
            INSERT INTO registros
            (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision)
            SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            WHERE NOT EXISTS (
                SELECT 1 FROM registros
                WHERE empresa = %s AND fecha = %s AND fuente = %s AND categoria = %s
            )
        """, (record[0], DEMO_COMPANY, *record[1:], DEMO_COMPANY, record[0], record[3], record[4]))

    cursor.execute("SELECT id FROM vehiculos WHERE empresa = %s AND patente = %s", (DEMO_COMPANY, "DEMO-01"))
    vehicle = cursor.fetchone()
    if vehicle:
        vehicle_id = vehicle[0]
    else:
        cursor.execute("""
            INSERT INTO vehiculos (empresa, patente, tipo, marca, modelo, anio, fecha_registro)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (DEMO_COMPANY, "DEMO-01", "Camioneta", "GreenTrack", "Demo X", 2024,
               datetime.now().strftime("%Y-%m-%d %H:%M")))
        vehicle_id = cursor.fetchone()[0]
    cursor.execute("""
        INSERT INTO combustible_movil
        (empresa, vehiculo_id, periodo, combustible, cantidad, unidad, costo, fecha_registro)
        SELECT %s, %s, %s, %s, %s, %s, %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM combustible_movil
            WHERE empresa = %s AND vehiculo_id = %s AND periodo = %s
        )
    """, (DEMO_COMPANY, vehicle_id, "2025-02", "Diésel", 800, "litros", 900000,
           datetime.now().strftime("%Y-%m-%d %H:%M"), DEMO_COMPANY, vehicle_id, "2025-02"))

    cursor.execute("""
        INSERT INTO medidas_productivas (empresa, anio, periodo, unidad, valor, fecha_actualizacion)
        VALUES (%s, 2025, '2025', %s, %s, %s)
        ON CONFLICT (empresa, periodo) DO UPDATE SET unidad = EXCLUDED.unidad, valor = EXCLUDED.valor
    """, (DEMO_COMPANY, "servicios prestados", 10000, datetime.now().strftime("%Y-%m-%d %H:%M")))
    cursor.execute("""
        INSERT INTO configuracion (empresa, info_por_unidad, vehiculos, combustible_colaboradores, tarjeta_combustible)
        VALUES (%s, 1, 1, 1, 1)
        ON CONFLICT (empresa) DO UPDATE SET info_por_unidad = EXCLUDED.info_por_unidad, vehiculos = EXCLUDED.vehiculos
    """, (DEMO_COMPANY,))

    cursor.execute("SELECT id FROM agua_sedes WHERE empresa = %s AND nombre_sede = %s", (DEMO_COMPANY, "Sede Demostración"))
    sede = cursor.fetchone()
    if sede:
        sede_id = sede[0]
    else:
        cursor.execute("""
            INSERT INTO agua_sedes
            (empresa, nombre_sede, region, comuna, latitud, longitud, codigo_cuenca, nombre_cuenca, nivel_ubicacion)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (DEMO_COMPANY, "Sede Demostración", "Metropolitana", "Santiago", -33.4489,
               -70.6693, "RIO-MAIPO", "Cuenca del río Maipo", "cuenca"))
        sede_id = cursor.fetchone()[0]
    cursor.execute("""
        INSERT INTO agua_flujos
        (empresa, sede_id, periodo, tipo_flujo, fuente_agua, origen_hidrico, destino_agua,
         volumen_m3, proceso_o_area, tiene_tratamiento, calidad_dato, observaciones)
        SELECT %s, %s, %s, 'captacion', 'Red pública', 'Sistema potable', 'Operación',
               %s, 'Operaciones', TRUE, 'Medido', 'Dato demostrativo'
        WHERE NOT EXISTS (
            SELECT 1 FROM agua_flujos WHERE empresa = %s AND sede_id = %s AND periodo = %s AND tipo_flujo = 'captacion'
        )
    """, (DEMO_COMPANY, sede_id, "2025-06-01", 420, DEMO_COMPANY, sede_id, "2025-06-01"))
    cursor.execute("""
        SELECT id FROM registros
        WHERE empresa = %s AND (LOWER(COALESCE(fuente, '')) LIKE '%%electricidad%%'
            OR LOWER(COALESCE(fuente, '')) LIKE '%%combusti%%'
            OR LOWER(COALESCE(fuente, '')) LIKE '%%combustible%%'
            OR LOWER(COALESCE(alcance, '')) = 'alcance 2')
    """, (DEMO_COMPANY,))
    for (registro_id,) in cursor.fetchall():
        calcular_huella_suministro_registro(cursor, registro_id)
    cursor.execute("INSERT INTO greentrack_demo_seed (id, version) VALUES (1, '2026-09-23-v1')")


def init_db ():
    conn =get_db ()
    cursor =conn .cursor ()

    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id SERIAL PRIMARY KEY,
        empresa TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        contacto TEXT,
        telefono TEXT,
        direccion TEXT,
        rut TEXT,
        es_admin INTEGER DEFAULT 0,
        fecha_registro TEXT,
        anio_default TEXT
    )
    """)
    cursor .execute ("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS anio_default TEXT")
    cursor .execute ("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS sector_empresa TEXT")
    cursor .execute ("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tipo_empresa TEXT")
    cursor .execute ("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS rol TEXT NOT NULL DEFAULT 'empresa'")
    cursor .execute ("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS es_demo BOOLEAN NOT NULL DEFAULT FALSE")
    cursor .execute ("UPDATE usuarios SET rol = CASE WHEN es_admin = 1 THEN 'admin' ELSE 'empresa' END WHERE rol IS NULL OR rol = ''")
    cursor .execute ('''
    CREATE TABLE IF NOT EXISTS registros (
        id SERIAL PRIMARY KEY,
        fecha TEXT,
        empresa TEXT,
        area TEXT,
        alcance TEXT,
        fuente TEXT,
        subfuente TEXT,
        categoria TEXT,
        actividad TEXT,
        identificador TEXT,
        detalle TEXT,
        unidad TEXT,
        cantidad REAL,
        costo REAL,
        moneda TEXT,
        factor REAL,
        emision REAL
    )
    ''')
    cursor.execute("ALTER TABLE IF EXISTS registros ADD COLUMN IF NOT EXISTS nivel_tension TEXT")
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS configuracion (
        id SERIAL PRIMARY KEY,
        empresa TEXT UNIQUE,
        info_por_unidad INTEGER,
        vehiculos INTEGER DEFAULT 0,
        combustible_colaboradores INTEGER,
        tarjeta_combustible INTEGER,
        FOREIGN KEY (empresa) REFERENCES usuarios(empresa)
    )
    """)
    cursor .execute ("ALTER TABLE IF EXISTS configuracion ADD COLUMN IF NOT EXISTS medicion_entrada_agua TEXT")
    cursor .execute ("ALTER TABLE IF EXISTS configuracion ADD COLUMN IF NOT EXISTS medicion_salida_agua TEXT")
    cursor .execute ("ALTER TABLE IF EXISTS configuracion ADD COLUMN IF NOT EXISTS estado_medicion_agua TEXT")
    cursor .execute ("ALTER TABLE IF EXISTS configuracion ADD COLUMN IF NOT EXISTS mensaje_medicion_agua TEXT")
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS medidas_productivas (
        id SERIAL PRIMARY KEY,
        empresa TEXT NOT NULL,
        anio INTEGER NOT NULL,
        periodo TEXT,
        unidad TEXT NOT NULL,
        valor REAL NOT NULL DEFAULT 0,
        fecha_actualizacion TEXT,
        UNIQUE (empresa, anio),
        FOREIGN KEY (empresa) REFERENCES usuarios(empresa)
    )
    """)
    cursor.execute("ALTER TABLE IF EXISTS medidas_productivas ADD COLUMN IF NOT EXISTS periodo TEXT")
    cursor.execute("UPDATE medidas_productivas SET periodo = anio::text WHERE periodo IS NULL OR periodo = ''")
    cursor.execute("ALTER TABLE IF EXISTS medidas_productivas ALTER COLUMN periodo SET NOT NULL")
    cursor.execute("ALTER TABLE IF EXISTS medidas_productivas DROP CONSTRAINT IF EXISTS medidas_productivas_empresa_anio_key")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS medidas_productivas_empresa_periodo_key ON medidas_productivas (empresa, periodo)")
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS energeticos_empresa (
        id SERIAL PRIMARY KEY,
        empresa TEXT,
        energetico TEXT,
        proveedor TEXT,
        documento TEXT,
        FOREIGN KEY (empresa) REFERENCES usuarios(empresa)
    )
    """)
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS factores (
        categoria TEXT,
        unidad TEXT,
        factor REAL,
        PRIMARY KEY (categoria, unidad)
    )
    """)
    cursor .execute ("CREATE TABLE IF NOT EXISTS agua_consumo (id SERIAL PRIMARY KEY, empresa TEXT, fecha TEXT, agua_embotellada_litros REAL, hielo_comprado_kg REAL, hielo_producido_kg REAL, tiene_tratamiento INTEGER DEFAULT 0, descripcion_tratamiento TEXT, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor .execute ("CREATE TABLE IF NOT EXISTS agua_afluentes (id SERIAL PRIMARY KEY, empresa TEXT, fecha TEXT, tipo TEXT, caudal_m3 REAL, tratamiento TEXT, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor .execute ("CREATE TABLE IF NOT EXISTS agua_cuencas (id SERIAL PRIMARY KEY, empresa TEXT, fecha TEXT, tipo_cuenca TEXT, cantidad_m3 REAL, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor .execute ("CREATE TABLE IF NOT EXISTS residuos_registros (id SERIAL PRIMARY KEY, empresa TEXT, fecha TEXT, periodo TEXT, tipo_residuo TEXT, cantidad_ton REAL, tratamiento TEXT, costo REAL, destino TEXT, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor .execute ("CREATE TABLE IF NOT EXISTS vehiculos (id SERIAL PRIMARY KEY, empresa TEXT, patente TEXT NOT NULL, tipo TEXT, marca TEXT, modelo TEXT, anio INTEGER, estado INTEGER DEFAULT 1, fecha_registro TEXT, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor .execute ("CREATE TABLE IF NOT EXISTS combustible_movil (id SERIAL PRIMARY KEY, empresa TEXT, vehiculo_id INTEGER, periodo TEXT, combustible TEXT, cantidad REAL, unidad TEXT, costo REAL, fecha_registro TEXT, registro_id INTEGER, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor.execute("ALTER TABLE IF EXISTS combustible_movil ADD COLUMN IF NOT EXISTS registro_id INTEGER")
    cursor .execute ("CREATE TABLE IF NOT EXISTS agua_costos (id SERIAL PRIMARY KEY, empresa TEXT, fecha TEXT, concepto TEXT, cantidad REAL, unidad TEXT, costo_usd REAL, costo_clp REAL, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS agua_sedes (
        id SERIAL PRIMARY KEY,
        empresa TEXT NOT NULL,
        nombre_sede TEXT NOT NULL,
        region TEXT,
        comuna TEXT,
        latitud NUMERIC(12, 8),
        longitud NUMERIC(12, 8),
        codigo_cuenca TEXT,
        nombre_cuenca TEXT,
        nivel_ubicacion TEXT,
        activo BOOLEAN DEFAULT TRUE,
        fecha_creacion TIMESTAMP NOT NULL DEFAULT NOW(),
        FOREIGN KEY (empresa) REFERENCES usuarios(empresa)
    )
    """)
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS agua_flujos (
        id SERIAL PRIMARY KEY,
        empresa TEXT NOT NULL,
        sede_id INTEGER,
        periodo DATE NOT NULL,
        tipo_flujo TEXT NOT NULL CHECK (tipo_flujo IN ('captacion', 'retorno', 'reuso')),
        fuente_agua TEXT,
        origen_hidrico TEXT,
        destino_agua TEXT,
        volumen_m3 NUMERIC(18, 6) NOT NULL DEFAULT 0,
        proceso_o_area TEXT,
        retorna_mismo_sistema_hidrico BOOLEAN DEFAULT FALSE,
        tiene_tratamiento BOOLEAN DEFAULT FALSE,
        calidad_dato TEXT,
        evidencia TEXT,
        observaciones TEXT,
        fecha_registro TIMESTAMP NOT NULL DEFAULT NOW(),
        FOREIGN KEY (empresa) REFERENCES usuarios(empresa),
        FOREIGN KEY (sede_id) REFERENCES agua_sedes(id)
    )
    """)
    cursor .execute ("ALTER TABLE IF EXISTS agua_flujos ALTER COLUMN sede_id DROP NOT NULL")
    cursor .execute ("ALTER TABLE IF EXISTS agua_flujos ADD COLUMN IF NOT EXISTS origen_hidrico TEXT")
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS factores_huella_suministro_agua (
        id SERIAL PRIMARY KEY,
        tipo_energia TEXT NOT NULL,
        categoria_factor TEXT NOT NULL,
        factor_m3_gj NUMERIC(18, 8) NOT NULL,
        fuente TEXT NOT NULL,
        referencia TEXT,
        version TEXT,
        activo BOOLEAN DEFAULT TRUE,
        fecha_carga TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE(tipo_energia, categoria_factor, version)
    )
    """)
    cursor.execute("ALTER TABLE IF EXISTS factores_huella_suministro_agua ALTER COLUMN factor_m3_gj DROP NOT NULL")
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS resultados_huella_suministro_agua (
        id SERIAL PRIMARY KEY,
        registro_id INTEGER UNIQUE,
        empresa TEXT NOT NULL,
        fecha DATE,
        fuente_consumo TEXT,
        categoria_consumo TEXT,
        unidad_consumo TEXT,
        cantidad_consumo NUMERIC(18, 6),
        energia_gj NUMERIC(18, 8),
        factor_id INTEGER,
        factor_m3_gj NUMERIC(18, 8),
        huella_suministro_m3 NUMERIC(18, 8),
        calidad_resultado TEXT,
        observaciones TEXT,
        fecha_calculo TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    for column, definition in (
        ("energy_id", "TEXT"),
        ("water_factor_id", "TEXT"),
        ("source_id", "TEXT"),
        ("dataset", "TEXT"),
        ("geography", "TEXT"),
        ("method", "TEXT"),
        ("water_quality", "TEXT"),
        ("warning", "TEXT"),
        ("source_unit", "TEXT"),
        ("normalized_quantity", "NUMERIC(18, 8)"),
        ("water_status", "TEXT"),
        ("nivel_tension", "TEXT"),
    ):
        cursor.execute(f"ALTER TABLE IF EXISTS resultados_huella_suministro_agua ADD COLUMN IF NOT EXISTS {column} {definition}")
    cursor.execute("ALTER TABLE IF EXISTS factores_huella_suministro_agua ADD COLUMN IF NOT EXISTS factor_code TEXT")
    cursor.execute("ALTER TABLE IF EXISTS factores_huella_suministro_agua ADD COLUMN IF NOT EXISTS factor_value NUMERIC(18, 12)")
    cursor.execute("ALTER TABLE IF EXISTS factores_huella_suministro_agua ADD COLUMN IF NOT EXISTS factor_unit TEXT")
    cursor.execute("ALTER TABLE IF EXISTS factores_huella_suministro_agua ADD COLUMN IF NOT EXISTS source_unit TEXT")
    cursor.execute("ALTER TABLE IF EXISTS factores_huella_suministro_agua ADD COLUMN IF NOT EXISTS dataset TEXT")
    cursor.execute("ALTER TABLE IF EXISTS factores_huella_suministro_agua ADD COLUMN IF NOT EXISTS geography TEXT")
    cursor.execute("ALTER TABLE IF EXISTS factores_huella_suministro_agua ADD COLUMN IF NOT EXISTS method TEXT")
    cursor.execute("ALTER TABLE IF EXISTS factores_huella_suministro_agua ADD COLUMN IF NOT EXISTS quality TEXT")
    cursor.execute("ALTER TABLE IF EXISTS factores_huella_suministro_agua ADD COLUMN IF NOT EXISTS source_id TEXT")
    cursor.execute("ALTER TABLE IF EXISTS factores_huella_suministro_agua ADD COLUMN IF NOT EXISTS water_status TEXT")
    _seed_factores_huella_suministro_agua (cursor )
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS factores_escasez_agua (
        id SERIAL PRIMARY KEY,
        metodo TEXT NOT NULL,
        version_metodo TEXT NOT NULL,
        actividad TEXT,
        nivel_geografico TEXT NOT NULL CHECK (nivel_geografico IN ('cuenca', 'subnacional', 'pais')),
        codigo_geografico TEXT NOT NULL,
        periodo_inicio DATE,
        periodo_fin DATE,
        factor_m3eq_m3 NUMERIC(18, 8) NOT NULL,
        fuente TEXT NOT NULL,
        referencia TEXT,
        fecha_carga TIMESTAMP NOT NULL DEFAULT NOW(),
        activo BOOLEAN DEFAULT TRUE
    )
    """)
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS resultados_huella_agua (
        id SERIAL PRIMARY KEY,
        empresa TEXT NOT NULL,
        sede_id INTEGER NOT NULL,
        periodo DATE NOT NULL,
        captacion_m3 NUMERIC(18, 6) NOT NULL DEFAULT 0,
        retorno_m3 NUMERIC(18, 6) NOT NULL DEFAULT 0,
        retorno_mismo_sistema_m3 NUMERIC(18, 6) NOT NULL DEFAULT 0,
        reuso_m3 NUMERIC(18, 6) NOT NULL DEFAULT 0,
        consumo_operativo_m3 NUMERIC(18, 6) NOT NULL DEFAULT 0,
        factor_escasez_aplicado NUMERIC(18, 8),
        huella_escasez_m3eq NUMERIC(18, 8),
        id_factor_escasez INTEGER,
        nivel_calculo TEXT NOT NULL,
        calidad_resultado TEXT,
        version_calculo TEXT NOT NULL,
        fecha_calculo TIMESTAMP NOT NULL DEFAULT NOW(),
        FOREIGN KEY (empresa) REFERENCES usuarios(empresa),
        FOREIGN KEY (sede_id) REFERENCES agua_sedes(id),
        FOREIGN KEY (id_factor_escasez) REFERENCES factores_escasez_agua(id),
        UNIQUE (empresa, sede_id, periodo, version_calculo)
    )
    """)
    cursor .execute ('''
    CREATE TABLE IF NOT EXISTS factores_electricos (
        anio INTEGER,
        mes INTEGER,
        sistema TEXT,
        factor_emision_avg REAL
    )
    ''')

    cursor .execute ("ALTER TABLE registros ADD COLUMN IF NOT EXISTS emision_ubicacion REAL DEFAULT 0")
    cursor .execute ("ALTER TABLE registros ADD COLUMN IF NOT EXISTS origen_energia TEXT")
    cursor .execute ("ALTER TABLE registros ADD COLUMN IF NOT EXISTS tiene_irec TEXT DEFAULT 'No'")
    cursor .execute ("ALTER TABLE registros ADD COLUMN IF NOT EXISTS sistema TEXT")
    cursor .execute ("ALTER TABLE registros ADD COLUMN IF NOT EXISTS codigo_residuo TEXT")
    cursor .execute ("ALTER TABLE registros ADD COLUMN IF NOT EXISTS residuo_oficial TEXT")
    cursor .execute ("ALTER TABLE registros ADD COLUMN IF NOT EXISTS tratamiento_normalizado TEXT")
    cursor .execute ("ALTER TABLE registros ADD COLUMN IF NOT EXISTS categoria_defra TEXT")
    cursor .execute ("ALTER TABLE registros ADD COLUMN IF NOT EXISTS tratamiento_defra TEXT")
    cursor .execute ("ALTER TABLE registros ADD COLUMN IF NOT EXISTS estado_validacion TEXT")
    cursor .execute ("ALTER TABLE registros ADD COLUMN IF NOT EXISTS observacion_factor TEXT")

    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS irec_certificados (
        id SERIAL PRIMARY KEY,
        empresa TEXT,
        fecha_consumo TEXT,
        filename TEXT,
        contenido BYTEA,
        fecha_subida TEXT
    )
    """)
    cursor .execute ("ALTER TABLE factores ADD COLUMN IF NOT EXISTS nombre_chile TEXT")
    cursor .execute ("ALTER TABLE factores ADD COLUMN IF NOT EXISTS tratamiento TEXT")
    cursor .execute ("ALTER TABLE factores ADD COLUMN IF NOT EXISTS anio INTEGER DEFAULT 0")
    cursor .execute ("UPDATE factores SET nombre_chile = NULL WHERE nombre_chile IN ('nan', '', 'None')")
    cursor .execute ("UPDATE factores SET tratamiento = NULL WHERE tratamiento IN ('nan', '', 'None')")
    # Migrar residuos DEFRA de unidad 'kg' a 'tonne' (factor es kg CO₂e/t, no por kg)
    cursor .execute ("""
        UPDATE factores SET unidad = 'tonne'
        WHERE unidad = 'kg' AND (nombre_chile IS NOT NULL OR tratamiento IS NOT NULL)
        AND (categoria, unidad, anio) NOT IN (SELECT categoria, 'tonne', anio FROM factores WHERE unidad = 'tonne')
    """)
    # Migrar PK a (categoria, unidad, anio) y luego extender para incluir tratamiento
    try :
        cursor .execute ("SAVEPOINT sp_factores_pk")
        cursor .execute ("ALTER TABLE factores DROP CONSTRAINT factores_pkey")
        cursor .execute ("RELEASE SAVEPOINT sp_factores_pk")
    except Exception :
        cursor .execute ("ROLLBACK TO SAVEPOINT sp_factores_pk")
        # Índice único con tratamiento: permite un factor por (categoria, unidad, anio, tratamiento)
    try :
        cursor .execute ("SAVEPOINT sp_factores_trat_idx")
        cursor .execute ("""
            CREATE UNIQUE INDEX IF NOT EXISTS factores_unique_trat
            ON factores (categoria, unidad, anio, COALESCE(tratamiento, ''))
        """)
        cursor .execute ("RELEASE SAVEPOINT sp_factores_trat_idx")
    except Exception :
        cursor .execute ("ROLLBACK TO SAVEPOINT sp_factores_trat_idx")

    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS pdf_uploads (
        id SERIAL PRIMARY KEY,
        empresa TEXT,
        fecha_subida TEXT,
        nombre_archivo TEXT,
        tipo TEXT,
        registros_generados INTEGER DEFAULT 0,
        sin_factor INTEGER DEFAULT 0
    )
    """)
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS pending_pdf_uploads (
        id SERIAL PRIMARY KEY,
        empresa TEXT,
        fecha_subida TEXT,
        nombre_archivo TEXT,
        tipo TEXT,
        datos_json TEXT,
        estado TEXT DEFAULT 'pendiente',
        motivo_rechazo TEXT,
        fecha_revision TEXT
    )
    """)

    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS tickets (
        id SERIAL PRIMARY KEY,
        empresa TEXT NOT NULL,
        asunto TEXT NOT NULL,
        descripcion TEXT NOT NULL,
        prioridad TEXT DEFAULT 'Normal',
        estado TEXT DEFAULT 'Abierto',
        respuesta TEXT,
        fecha_creacion TEXT NOT NULL,
        fecha_respuesta TEXT
    )
    """)

    # Normalizar alcance existente basado en fuente (cubre NULL y valores incorrectos)
    cursor .execute ("""
        UPDATE registros SET alcance = 'Alcance 1'
        WHERE fuente IN ('Combustión Fija','Combustible Móvil','Combustión Estacionaria','Refrigerantes','Fugas de Refrigerantes')
          AND (alcance IS NULL OR alcance NOT IN ('Alcance 1','Alcance 2','Alcance 3'))
    """)
    cursor .execute ("""
        UPDATE registros SET alcance = 'Alcance 2'
        WHERE fuente = 'Electricidad'
          AND (alcance IS NULL OR alcance NOT IN ('Alcance 1','Alcance 2','Alcance 3'))
    """)
    cursor .execute ("""
        UPDATE registros SET alcance = 'Alcance 3'
        WHERE fuente = 'Residuos'
          AND (alcance IS NULL OR alcance NOT IN ('Alcance 1','Alcance 2','Alcance 3'))
    """)

    _seed_admin_account(cursor)
    _seed_demo_account(cursor)

    conn .commit ()
    conn .close ()

def _guardar_estado_medicion_agua(cursor, empresa, entrada, salida, estado):
    estado = diagnosticar(entrada,salida)
    cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('agua:'+empresa,))
    mensajes = MENSAJES
    cursor.execute("""
        INSERT INTO configuracion (
            empresa, info_por_unidad, vehiculos, combustible_colaboradores,
            tarjeta_combustible, medicion_entrada_agua, medicion_salida_agua,
            estado_medicion_agua, mensaje_medicion_agua
        )
        VALUES (%s, 0, 0, 0, 0, %s, %s, %s, %s)
        ON CONFLICT (empresa) DO UPDATE SET
            medicion_entrada_agua = EXCLUDED.medicion_entrada_agua,
            medicion_salida_agua = EXCLUDED.medicion_salida_agua,
            estado_medicion_agua = EXCLUDED.estado_medicion_agua,
            mensaje_medicion_agua = EXCLUDED.mensaje_medicion_agua
    """, (empresa, entrada, salida, estado, mensajes.get(estado)))
    return mensajes.get(estado)


def _estado_medicion_agua(cursor, empresa):
    return leer_diagnostico(cursor,empresa)


# ================= DASHBOARD PRINCIPAL =================
@app .route ("/dashboard")
def dashboard ():
    if 'user_id'not in session or session .get ('es_admin')==1 :return redirect ("/")
    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    estado_medicion_agua, mensaje_medicion_agua = _estado_medicion_agua(cursor, empresa)

    # 1. BUSCADOR DE AÑOS
    cursor .execute ("""
        SELECT DISTINCT SUBSTRING(fecha::text, 1, 4) as anio FROM registros 
        WHERE empresa = %s AND fecha IS NOT NULL AND length(fecha::text) >= 4
        UNION
        SELECT DISTINCT SUBSTRING(fecha_registro::text, 1, 4) FROM combustible_movil 
        WHERE empresa = %s AND fecha_registro IS NOT NULL AND length(fecha_registro::text) >= 4
        ORDER BY anio DESC
    """,(empresa ,empresa ))
    anios_disponibles =[row ['anio']for row in cursor .fetchall ()if row ['anio']]

    cursor .execute ("SELECT anio_default FROM usuarios WHERE id = %s",(session ['user_id'],))
    pref =cursor .fetchone ()
    anio_default =pref ['anio_default']if pref and pref ['anio_default']else 'Todos'
    anio_seleccionado =request .args .get ('anio',anio_default )
    if anio_seleccionado != 'Todos' and (not re.fullmatch(r'\d{4}',str(anio_seleccionado)) or int(anio_seleccionado)<1):
        flash('Selecciona un año válido.','warning')
        anio_seleccionado = 'Todos'
    params_reg =[empresa ]
    params_comb =[empresa ]
    filtro_fecha_reg =""
    filtro_fecha_comb =""

    # FILTRO: Usamos cm.fecha_registro para evitar ambigüedades en el JOIN
    if anio_seleccionado !='Todos':
        filtro_fecha_reg =" AND SUBSTRING(fecha::text, 1, 4) = %s"
        filtro_fecha_comb =" AND SUBSTRING(cm.fecha_registro::text, 1, 4) = %s"
        params_reg .append (anio_seleccionado )
        params_comb .append (anio_seleccionado )

        # 2. TOTALES Y VEHÍCULOS
    cursor .execute (f"SELECT SUM(emision) FROM registros WHERE empresa = %s {filtro_fecha_reg }",params_reg )
    res_reg =cursor .fetchone ()
    total_reg =float (res_reg [0 ])if res_reg and res_reg [0 ]else 0.0 
    total_comb =0.0 # incluido en registros desde ahora

    # Breakdown por vehículo (para la tarjeta de flota en el dashboard)
    cursor .execute (f"SELECT r.emision, v.patente, v.tipo FROM combustible_movil cm JOIN registros r ON r.id = cm.registro_id AND r.empresa = cm.empresa LEFT JOIN vehiculos v ON cm.vehiculo_id = v.id AND v.empresa = cm.empresa WHERE cm.empresa = %s {filtro_fecha_comb }",params_comb )
    comb_data =cursor .fetchall ()
    vehiculos_dict ={}
    for row in comb_data :
        emision =float(row['emision'] or 0)
        pat =row ['patente']or 'Sin Asignar'
        if pat not in vehiculos_dict :
            vehiculos_dict [pat ]={'patente':pat ,'tipo':row ['tipo']or 'N/A','registros':0 ,'total_emision':0.0 }
        vehiculos_dict [pat ]['registros']+=1 
        vehiculos_dict [pat ]['total_emision']+=emision 
    total_emision_flota =sum(row['total_emision'] for row in vehiculos_dict.values())

    cursor .execute ("SELECT * FROM vehiculos WHERE empresa = %s",(empresa ,))
    vehiculos =cursor .fetchall ()

    # 3. GRÁFICO (Conversión a Float para no romper el JSON)
    cursor .execute (f"SELECT fuente, SUM(emision) as total FROM registros WHERE empresa = %s {filtro_fecha_reg } GROUP BY fuente",params_reg )
    categorias =[{'fuente':row ['fuente'],'total':float (row ['total'])if row ['total']else 0.0 }for row in cursor .fetchall ()]

    # 4. CONTEO TOTAL Y ÚLTIMOS REGISTROS
    cursor .execute (f"SELECT COUNT(*) FROM registros WHERE empresa = %s {filtro_fecha_reg }",params_reg )
    total_registros =cursor .fetchone ()[0 ]or 0 

    cursor .execute (f"""
        SELECT id, fecha, fuente, actividad, cantidad, unidad, emision, 'manual' as tipo_tabla
        FROM registros WHERE empresa = %s {filtro_fecha_reg }
        ORDER BY fecha DESC LIMIT 5
    """,params_reg )
    ultimos =[dict (row )for row in cursor .fetchall ()]

    # 5. TENDENCIA MENSUAL POR ALCANCE
    # La tabla maestra ya contiene los consumos móviles.
    params_tendencia =params_reg

    cursor .execute (f"""
        SELECT mes, alcance, SUM(total) as total
        FROM (
            SELECT
                SUBSTRING(fecha::text, 1, 7) as mes,
                CASE
                    WHEN fuente IN (
                        'Combustión Estacionaria', 'Combustión Fija', 'Combustible Móvil', 'Combustión Móvil',
                        'Combustión Estacionaria', 'Combustión Fija', 'Combustible Móvil', 'Combustión Móvil',
                        'Refrigerantes', 'Fugas de Refrigerantes'
                    ) THEN 1
                    WHEN fuente = 'Electricidad' THEN 2
                    WHEN fuente = 'Residuos' THEN 3
                    ELSE 1
                END as alcance,
                COALESCE(emision, 0) as total
            FROM registros
            WHERE empresa = %s {filtro_fecha_reg }

        ) t
        GROUP BY mes, alcance
        ORDER BY mes
    """,params_tendencia )
    tendencia_reg =cursor .fetchall ()

    from collections import defaultdict 
    meses_dict =defaultdict (lambda :{1 :0.0 ,2 :0.0 ,3 :0.0 })
    for row in tendencia_reg :
        meses_dict [row ['mes']][row ['alcance']]+=float (row ['total']or 0 )

    meses_sorted =sorted (meses_dict .keys ())
    tendencia_labels =meses_sorted 
    tendencia_a1 =[round (meses_dict [m ][1 ],2 )for m in meses_sorted ]
    tendencia_a2 =[round (meses_dict [m ][2 ],2 )for m in meses_sorted ]
    tendencia_a3 =[round (meses_dict [m ][3 ],2 )for m in meses_sorted ]

    alcance_a1 =round (sum (meses_dict[m][1] for m in meses_sorted),2 )
    alcance_a2 =round (sum (meses_dict[m][2] for m in meses_sorted),2 )
    alcance_a3 =round (sum (meses_dict[m][3] for m in meses_sorted),2 )
    alcances_data =[
    {'nombre':'Alcance 1 - Directo','total':alcance_a1 ,'color':'#EF4444'},
    {'nombre':'Alcance 2 - Electricidad','total':alcance_a2 ,'color':'#F59E0B'},
    {'nombre':'Alcance 3 - Residuos','total':alcance_a3 ,'color':'#10B981'},
    ]

    cursor .execute ("""
        SELECT anio, periodo, unidad, valor, fecha_actualizacion
        FROM medidas_productivas
        WHERE empresa = %s AND periodo = anio::text
        ORDER BY anio DESC, id DESC
    """,(empresa ,))
    medidas_productivas =[dict (row )for row in cursor .fetchall ()]
    intensidad_anio =None 
    intensidad_unidad =None 
    intensidad_emisiones =None
    intensidad_medida =0.0 

    medida_intensidad =None 
    if anio_seleccionado !='Todos':
        try :
            anio_int =int (anio_seleccionado )
        except Exception :
            anio_int =None 
        intensidad_anio =anio_int 
        if anio_int is not None :
            medida_intensidad =next ((m for m in medidas_productivas if int (m .get ('anio')or 0 )==anio_int ),None )
    else :
        medida_intensidad =medidas_productivas [0 ]if medidas_productivas else None 
        if medida_intensidad :
            intensidad_anio =int (medida_intensidad .get ('anio')or 0 )or None 

    if medida_intensidad :
        intensidad_unidad =medida_intensidad .get ('unidad')
        try :
            intensidad_medida =float (medida_intensidad .get ('valor')or 0 )
        except Exception :
            intensidad_medida =0.0 
        if intensidad_anio and intensidad_medida >0 :
            _ ,intensidad_emisiones =calcular_intensidad_emisiones (conn ,empresa ,intensidad_anio ,intensidad_medida )

    resumen_agua = crear_resumen_agua(conn,empresa,None if anio_seleccionado=='Todos' else anio_seleccionado)
    estado_medicion_agua = resumen_agua['estado_medicion_agua']
    mensaje_medicion_agua = resumen_agua['mensaje_medicion_agua']
    huella_azul_directa = resumen_agua['consumo']
    huella_verde_directa = 0.0
    huella_suministro_energetico = resumen_agua['huella_suministro_total']
    huella_hidrica_total = resumen_agua['huella_hidrica_total']
    intensidad_hidrica_total = resumen_agua['intensidad_hidrica_total']
    huella_hidrica_tipos = [
        {"tipo":"Huella azul directa","valor":huella_azul_directa,"color":"#2487F3"},
        {"tipo":"Huella verde directa","valor":huella_verde_directa,"color":"#22C55E"},
        {"tipo":"Suministro energetico","valor":huella_suministro_energetico,"color":"#9B5CF6"},
    ] if huella_hidrica_total is not None else []

            # 6. VARIACIÓN INTERANUAL
    variacion_pct =None 
    anio_prev =None 
    if anio_seleccionado !='Todos'and anio_seleccionado :
        anio_prev =str (int (anio_seleccionado )-1 )
        cursor .execute ("SELECT SUM(emision) FROM registros WHERE empresa = %s AND SUBSTRING(fecha::text, 1, 4) = %s",(empresa ,anio_prev ))
        res_p =cursor .fetchone ()
        total_prev =float (res_p [0 ])if res_p and res_p [0 ]else 0.0 
        total_actual =total_reg 
        if total_prev >0 :
            variacion_pct =round ((total_actual -total_prev )/total_prev *100 ,1 )

    conn .close ()

    return render_template ("dashboard.html",
    total_emision =total_reg +total_comb ,total_emision_registros =total_reg ,total_emision_combustible =total_comb ,
    total_emision_flota=total_emision_flota,
    categorias_data =categorias ,ultimos_registros =ultimos ,total_registros =total_registros ,empresa =empresa ,
    anios_disponibles =anios_disponibles ,anio_seleccionado =anio_seleccionado ,
    vehiculos_emisiones =list (vehiculos_dict .values ()),tiene_vehiculos =len (vehiculos )>0 ,vehiculos =vehiculos ,
    tendencia_labels =tendencia_labels ,tendencia_a1 =tendencia_a1 ,
    tendencia_a2 =tendencia_a2 ,tendencia_a3 =tendencia_a3 ,
    alcances_data =alcances_data ,variacion_pct =variacion_pct ,anio_prev =anio_prev ,
    intensidad_emisiones =intensidad_emisiones ,intensidad_unidad =intensidad_unidad ,
    huella_hidrica_total =huella_hidrica_total ,huella_azul_directa =huella_azul_directa ,
    huella_verde_directa =huella_verde_directa ,huella_suministro_energetico =huella_suministro_energetico ,
    huella_hidrica_tipos =huella_hidrica_tipos ,
    intensidad_hidrica_total =intensidad_hidrica_total ,
    intensidad_anio =intensidad_anio ,
    suministro_disponible=resumen_agua['suministro_disponible'],
    suministro_completo=resumen_agua['suministro_completo'],
    consumos_energeticos=resumen_agua['consumos_energeticos'],
    faltantes_suministro=resumen_agua['faltantes_suministro'],
    estado_medicion_agua =estado_medicion_agua ,
    mensaje_medicion_agua =mensaje_medicion_agua )

    # ================= HISTORIAL COMPLETO =================
@app .route ("/historial")
def historial ():
    if 'user_id'not in session :return redirect ("/")
    empresa =session .get ('empresa')
    POR_PAGINA =50 
    pagina =max (1 ,int (request .args .get ('pagina',1 )))

    filtro_alcance =request .args .get ('alcance','').strip ()
    filtro_fuente =request .args .get ('fuente','').strip ()
    filtro_anio =request .args .get ('anio','').strip ()
    filtro_q =request .args .get ('q','').strip ()

    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )

    if 'anio' not in request.args:
        anio_preferido = _anio_panel(cursor)
        filtro_anio = '' if anio_preferido == 'Todos' else anio_preferido

    if filtro_anio == 'Todos':
        filtro_anio = ''

    # Opciones para los filtros desplegables
    alcances_disponibles =['Alcance 1','Alcance 2','Alcance 3']
    cursor .execute ("SELECT DISTINCT fuente FROM registros WHERE empresa = %s AND fuente IS NOT NULL ORDER BY fuente",(empresa ,))
    fuentes_disponibles =[r [0 ]for r in cursor .fetchall ()]
    cursor .execute ("SELECT DISTINCT SUBSTRING(fecha::text,1,4) as anio FROM registros WHERE empresa = %s AND fecha IS NOT NULL ORDER BY anio DESC",(empresa ,))
    anios_disponibles =[r [0 ]for r in cursor .fetchall ()]

    _fuentes_a1 =('Combustión Fija','Combustible Móvil','Combustión Estacionaria','Refrigerantes','Fugas de Refrigerantes')
    _fuentes_a2 =('Electricidad',)
    _fuentes_a3 =('Residuos',)

    # WHERE dinámico
    where =["empresa = %s"]
    params =[empresa ]
    if filtro_alcance =='Alcance 1':
        where .append ("fuente IN %s");params .append (_fuentes_a1 )
    elif filtro_alcance =='Alcance 2':
        where .append ("fuente IN %s");params .append (_fuentes_a2 )
    elif filtro_alcance =='Alcance 3':
        where .append ("fuente IN %s");params .append (_fuentes_a3 )
    if filtro_fuente :
        where .append ("fuente = %s");params .append (filtro_fuente )
    if filtro_anio :
        where .append ("SUBSTRING(fecha::text,1,4) = %s");params .append (filtro_anio )
    if filtro_q :
        where .append ("(LOWER(fuente) LIKE %s OR LOWER(actividad) LIKE %s OR LOWER(categoria) LIKE %s)")
        like =f"%{filtro_q .lower ()}%"
        params +=[like ,like ,like ]

    where_sql =" AND ".join (where )

    cursor .execute (f"SELECT COUNT(*) FROM registros WHERE {where_sql }",params )
    total =cursor .fetchone ()[0 ]
    total_paginas =max (1 ,(total +POR_PAGINA -1 )//POR_PAGINA )
    pagina =min (pagina ,total_paginas )
    offset =(pagina -1 )*POR_PAGINA 

    cursor .execute (f"""
        SELECT id, fecha, alcance, fuente, categoria, actividad, cantidad, unidad, emision, 'manual' as tipo_tabla
        FROM registros WHERE {where_sql }
        ORDER BY fecha DESC, id DESC
        LIMIT %s OFFSET %s
    """,params +[POR_PAGINA ,offset ])

    registros =[dict (row )for row in cursor .fetchall ()]
    conn .close ()
    return render_template ("historial.html",registros =registros ,empresa =empresa ,
    pagina =pagina ,total_paginas =total_paginas ,total =total ,
    alcances_disponibles =alcances_disponibles ,
    fuentes_disponibles =fuentes_disponibles ,
    anios_disponibles =anios_disponibles ,
    filtro_alcance =filtro_alcance ,filtro_fuente =filtro_fuente ,
    filtro_anio =filtro_anio ,filtro_q =filtro_q )


    # ================= PANELES INDIVIDUALES =================
def _anio_panel(cursor):
    """Prioriza el filtro explícito; en su ausencia usa la preferencia de cuenta."""
    anio = request.args.get('anio')
    if anio is None:
        cursor.execute("SELECT anio_default FROM usuarios WHERE id = %s AND empresa = %s",
                       (session['user_id'], session.get('empresa')))
        row = cursor.fetchone()
        anio = row[0] if row and row[0] else 'Todos'
    anio = str(anio).strip() or 'Todos'
    if anio != 'Todos' and (not re.fullmatch(r'\d{4}', anio) or int(anio) < 1):
        flash('Selecciona un año válido.', 'warning')
        return 'Todos'
    return anio


def _filtro_panel(cursor):
    anio = _anio_panel(cursor)
    cursor.execute("SELECT DISTINCT SUBSTRING(fecha::text,1,4) FROM registros WHERE empresa = %s AND fecha IS NOT NULL ORDER BY 1 DESC",
                   (session.get('empresa'),))
    anios = sorted({r[0] for r in cursor.fetchall() if r[0]} | ({anio} if anio != 'Todos' else set()), reverse=True)
    return anio, anios


@app .route ("/combustion")
def combustion_dashboard ():
    if 'user_id'not in session :
        return redirect ("/")

    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    anio_seleccionado, anios_disponibles = _filtro_panel(cursor)
    filtro_anio_sql = '' if anio_seleccionado == 'Todos' else ' AND SUBSTRING(fecha::text,1,4) = %s'
    params_anio = () if anio_seleccionado == 'Todos' else (anio_seleccionado,)


    fuentes_comb =(
    'Combustión Estacionaria','Combustión Fija','Combustión Móvil','Combustible Móvil',
    'Combustión Estacionaria','Combustión Fija','Combustión Móvil','Combustible Móvil'
    )
    fuentes_fijas =('Combustión Estacionaria','Combustión Fija','Combustión Estacionaria','Combustión Fija')
    fuentes_moviles =('Combustión Móvil','Combustible Móvil','Combustión Móvil','Combustible Móvil')

    cursor .execute (f"SELECT fecha, fuente, categoria, actividad, cantidad, unidad, emision FROM registros WHERE empresa = %s AND fuente IN %s{filtro_anio_sql} ORDER BY fecha DESC LIMIT 10",(empresa ,fuentes_comb ) + params_anio)
    registros_comb =[dict (row )for row in cursor .fetchall ()]

    cursor .execute (f"SELECT categoria, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente IN %s{filtro_anio_sql} GROUP BY categoria",(empresa ,fuentes_comb ) + params_anio)
    grafico_data =[dict (row )for row in cursor .fetchall ()]

    cursor .execute (f"SELECT SUM(emision) FROM registros WHERE empresa = %s AND fuente IN %s{filtro_anio_sql}",(empresa ,fuentes_comb ) + params_anio)
    res =cursor .fetchone ()
    total_emision =res [0 ]if res and res [0 ]else 0 

    cursor .execute (f"""
        SELECT SUBSTRING(fecha::text, 1, 7) as mes, fuente, SUM(emision) as total
        FROM registros WHERE empresa = %s AND fuente IN %s{filtro_anio_sql}
        GROUP BY 1, 2 ORDER BY 1
    """,(empresa ,fuentes_comb ) + params_anio)
    tendencia_comb_raw =cursor .fetchall ()
    meses_comb =sorted ({row ['mes']for row in tendencia_comb_raw })
    tendencia_fija =[]
    tendencia_movil =[]
    for m in meses_comb :
        fija =sum (float (r ['total']or 0 )for r in tendencia_comb_raw if r ['mes']==m and r ['fuente'] in fuentes_fijas)
        movil =sum (float (r ['total']or 0 )for r in tendencia_comb_raw if r ['mes']==m and r ['fuente'] in fuentes_moviles)
        tendencia_fija .append (fija)
        tendencia_movil .append (movil)

    conn .close ()
    return render_template ("combustion_dashboard.html", anio_seleccionado=anio_seleccionado, anios_disponibles=anios_disponibles,registros =registros_comb ,grafico_data =grafico_data ,
    total_emision =total_emision ,meses_comb =meses_comb ,
    tendencia_fija =tendencia_fija ,tendencia_movil =tendencia_movil )

@app .route ("/electricidad")
def electricidad_dashboard ():
    if 'user_id'not in session :return redirect ("/")

    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    anio_seleccionado, anios_disponibles = _filtro_panel(cursor)
    filtro_anio_sql = '' if anio_seleccionado == 'Todos' else ' AND SUBSTRING(fecha::text,1,4) = %s'
    params_anio = () if anio_seleccionado == 'Todos' else (anio_seleccionado,)


    cursor .execute (
    "SELECT fecha, categoria, actividad, cantidad, unidad, emision, emision_ubicacion, "
    "origen_energia, tiene_irec, COALESCE(sistema, '') as sistema, factor "
    f"FROM registros WHERE empresa = %s AND fuente = 'Electricidad'{filtro_anio_sql} ORDER BY fecha DESC LIMIT 20",
    (empresa ,) + params_anio
    )
    rows =cursor .fetchall ()
    registros_elec =[]
    for r in rows :
        d =dict (r )
        cant =d ['cantidad']or 0 
        d ['factor_ubicacion']=round (d ['emision_ubicacion']/cant ,6 )if cant else 0.0 
        registros_elec .append (d )
    cursor .execute (
    f"SELECT sistema, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad'{filtro_anio_sql} GROUP BY sistema",
    (empresa ,) + params_anio
    )
    grafico_data_raw =cursor .fetchall ()
    if not grafico_data_raw :
        cursor .execute (
        f"SELECT categoria, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad'{filtro_anio_sql} GROUP BY categoria",
        (empresa ,) + params_anio
        )
        grafico_data_raw =cursor .fetchall ()
    grafico_data =[dict (r )for r in grafico_data_raw ]

    cursor .execute (f"SELECT SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad'{filtro_anio_sql}",(empresa ,) + params_anio)
    res =cursor .fetchone ()
    total_mercado =float (res ['total'])if res and res ['total']else 0.0 

    cursor .execute (f"SELECT SUM(emision_ubicacion) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad'{filtro_anio_sql}",(empresa ,) + params_anio)
    res2 =cursor .fetchone ()
    total_ubicacion =float (res2 ['total'])if res2 and res2 ['total']else 0.0 

    cursor .execute (f"SELECT SUM(cantidad) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad'{filtro_anio_sql}",(empresa ,) + params_anio)
    res3 =cursor .fetchone ()
    total_kwh =float (res3 ['total'])if res3 and res3 ['total']else 0.0 

    cursor .execute (f"SELECT COUNT(*) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad' AND tiene_irec = 'Si'{filtro_anio_sql}",(empresa ,) + params_anio)
    total_irec =cursor .fetchone ()['total']or 0 

    cursor .execute (f"""
        SELECT SUBSTRING(fecha::text, 1, 7) as mes,
               SUM(cantidad) as kwh,
               SUM(emision) as emision_mercado,
               SUM(emision_ubicacion) as emision_ubicacion
        FROM registros WHERE empresa = %s AND fuente = 'Electricidad'{filtro_anio_sql}
        GROUP BY 1 ORDER BY 1
    """,(empresa ,) + params_anio)
    tendencia_elec =[dict (r )for r in cursor .fetchall ()]

    conn .close ()
    return render_template (
    "electricidad_dashboard.html", anio_seleccionado=anio_seleccionado, anios_disponibles=anios_disponibles,
    registros =registros_elec ,
    grafico_data =grafico_data ,
    total_emision =total_mercado ,
    total_mercado =total_mercado ,
    total_ubicacion =total_ubicacion ,
    total_kwh =total_kwh ,
    total_irec =total_irec ,
    tendencia_elec =tendencia_elec ,
    )

def _agua_es_admin ():
    return session .get ("es_admin")==1 


def _agua_puede_ver_empresa (empresa_objivo ):
    return _agua_es_admin ()or session .get ("empresa")==empresa_objivo 


def _agua_periodo_mes (periodo ):
    if not periodo :
        return None 
    if isinstance (periodo ,str )and len (periodo )>=7 :
        return periodo [:7 ]
    return str (periodo )[:7 ]


def _agua_calcular_resumen(conn, empresa, periodo=None):
    return crear_resumen_agua(conn,empresa,periodo)


def _agua_consolidar_grupo_seguro(grupo,sede,factores,periodo_sel,medida_valor=None):
    return consolidar_grupo_seguro(grupo,sede,factores,periodo_sel,medida_valor)


def _agua_agrupar_resultados_reportes(flujos,sedes,factores,medida_valor=None,vista="mensual"):
    resultados = []
    for row in resultados_por_periodo(flujos,sedes,factores,medida_valor,vista):
        factor = row.get("factor")
        resultados.append({
            **{k:(float(v) if isinstance(v,Decimal) else v) for k,v in row.items() if k!="factor"},
            "factor_metodo":factor.metodo if factor else None,
            "factor_version":factor.version_metodo if factor else None,
            "factor_codigo":factor.codigo_geografico if factor else None,
            "factor_fuente":factor.fuente if factor else None,
            "factor_vigencia_inicio":factor.periodo_inicio if factor else None,
            "factor_vigencia_fin":factor.periodo_fin if factor else None,
        })
    return resultados


@app .route ("/agua")
def agua_dashboard ():
    if 'user_id'not in session :
        return redirect ("/")
    empresa =session .get ('empresa')
    try:
        periodo =normalizar_periodo(request.args.get('periodo'))
    except ValueError:
        flash('Selecciona un período de agua válido.','error')
        return redirect(url_for('agua_dashboard'))
    conn =get_db ()
    cursor =conn .cursor ()
    if 'periodo' not in request.args:
        anio_preferido = _anio_panel(cursor)
        periodo = None if anio_preferido == 'Todos' else anio_preferido
    estado_medicion_agua, mensaje_medicion_agua = _estado_medicion_agua(cursor, empresa)
    cursor .execute (
    """
        SELECT periodo FROM (
            SELECT DISTINCT to_char(periodo, 'YYYY-MM') AS periodo
            FROM agua_flujos WHERE empresa = %s
            UNION
            SELECT DISTINCT SUBSTRING(fecha::text,1,7) AS periodo
            FROM registros
            WHERE empresa = %s AND (fuente='Electricidad' OR LOWER(fuente) LIKE %s)
        ) periodos
        ORDER BY periodo DESC
    """,
    (empresa, empresa,'%combusti%'),
    )
    meses_disponibles = [r[0] for r in cursor.fetchall() if r[0]]
    periodos_disponibles = sorted(set(meses_disponibles) | {m[:4] for m in meses_disponibles} | ({periodo} if periodo else set()), reverse=True)
    data =_agua_calcular_resumen (conn ,empresa ,periodo )
    conn .close ()
    return render_template (
    "agua_dashboard.html",
    empresa =empresa ,
    periodo =periodo ,
    periodos_disponibles =periodos_disponibles ,
    menu_activo ="resumen",
    historia_url =url_for ("agua_reporte"),
    **data ,
    )

@app .route ("/refrigerantes")
def refrigerantes_dashboard ():
    if 'user_id'not in session :return redirect ("/")

    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    anio_seleccionado, anios_disponibles = _filtro_panel(cursor)
    filtro_anio_sql = '' if anio_seleccionado == 'Todos' else ' AND SUBSTRING(fecha::text,1,4) = %s'
    params_anio = () if anio_seleccionado == 'Todos' else (anio_seleccionado,)


    fuentes_ref =('Refrigerantes','Fugas de Refrigerantes')
    cursor .execute (f"SELECT SUM(emision) as total FROM registros WHERE empresa = %s AND fuente IN %s{filtro_anio_sql}",(empresa ,fuentes_ref ) + params_anio)
    res =cursor .fetchone ()
    total_refrigerantes =res ['total']if res and res ['total']else 0.0 

    cursor .execute (f"SELECT categoria, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente IN %s{filtro_anio_sql} GROUP BY categoria ORDER BY total DESC",(empresa ,fuentes_ref ) + params_anio)
    datos_gases =[dict (row )for row in cursor .fetchall ()]

    cursor .execute (f"SELECT SUBSTRING(fecha, 1, 7) as mes, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente IN %s{filtro_anio_sql} GROUP BY mes ORDER BY mes",(empresa ,fuentes_ref ) + params_anio)
    datos_meses =[dict (row )for row in cursor .fetchall ()]

    cursor .execute (f"SELECT fecha, categoria, cantidad, unidad, factor, emision FROM registros WHERE empresa = %s AND fuente IN %s{filtro_anio_sql} ORDER BY fecha DESC LIMIT 10",(empresa ,fuentes_ref ) + params_anio)
    rows =cursor .fetchall ()

    # El historial y los agregados usan la misma emisión almacenada.
    ultimos_registros =[dict(row) for row in rows]

    cursor .execute (f"SELECT COUNT(DISTINCT categoria) as total FROM registros WHERE empresa = %s AND fuente IN %s{filtro_anio_sql}",(empresa ,fuentes_ref ) + params_anio)
    res_gases =cursor .fetchone ()
    total_gases_distintos =res_gases ['total']if res_gases and res_gases ['total']else 0 

    gas_top =datos_gases [0 ]if datos_gases else None 

    conn .close ()
    return render_template ("refrigerantes_dashboard.html", anio_seleccionado=anio_seleccionado, anios_disponibles=anios_disponibles,total_refrigerantes =total_refrigerantes ,
    datos_gases =datos_gases ,datos_meses =datos_meses ,registros =ultimos_registros ,
    total_gases_distintos =total_gases_distintos ,gas_top =gas_top )


    # ================= GESTIÓN DE RESIDUOS (EL EXTRACTOR MÁGICO) =================
@app .route ('/residuos',methods =['GET'])
def residuos_dashboard ():
    if 'user_id'not in session :return redirect ("/")
    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    anio_seleccionado, anios_disponibles = _filtro_panel(cursor)
    filtro_anio_sql = '' if anio_seleccionado == 'Todos' else ' AND SUBSTRING(fecha::text,1,4) = %s'
    params_anio = () if anio_seleccionado == 'Todos' else (anio_seleccionado,)


    cursor .execute (f"SELECT SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Residuos'{filtro_anio_sql}",(empresa ,) + params_anio)
    res =cursor .fetchone ()
    total =float (res ['total'])if res and res ['total']else 0.0 

    cursor .execute (f"SELECT categoria, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Residuos'{filtro_anio_sql} GROUP BY categoria",(empresa ,) + params_anio)
    datos_cat =[{'categoria':row ['categoria'],'total':float (row ['total'])if row ['total']else 0.0 }for row in cursor .fetchall ()]

    cursor .execute (f"SELECT SUBSTRING(fecha, 1, 7) as mes, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Residuos'{filtro_anio_sql} GROUP BY mes ORDER BY mes",(empresa ,) + params_anio)
    datos_mes =[{'mes':row ['mes'],'total':float (row ['total'])if row ['total']else 0.0 }for row in cursor .fetchall ()]

    cursor .execute (f"""
        SELECT COALESCE(NULLIF(TRIM(actividad), ''), 'Sin especificar') as trat,
               SUM(emision) as total
        FROM registros WHERE empresa = %s AND fuente = 'Residuos'{filtro_anio_sql}
        GROUP BY 1 ORDER BY 2 DESC
    """,(empresa ,) + params_anio)
    datos_tratamiento =[{'tratamiento':row ['trat'],'total':float (row ['total'])if row ['total']else 0.0 }for row in cursor .fetchall ()]

    conn .close ()
    return render_template ("residuos.html", anio_seleccionado=anio_seleccionado, anios_disponibles=anios_disponibles,total_emision =total ,datos_categoria =datos_cat ,
    datos_mes =datos_mes ,datos_tratamiento =datos_tratamiento )

    # ================= FORMULARIO RESIDUOS (CON BUSCADOR INTELIGENTE) =================
@app .route ("/formulario_residuos",methods =['GET','POST'])
def formulario_residuos ():
    import re 
    import pandas as pd 
    import unicodedata 
    from datetime import datetime 

    if 'user_id'not in session :return redirect ("/")

    def limpiar_texto (texto ):
        if not texto :return ""
        texto =str (texto ).lower ().strip ()
        return ''.join (c for c in unicodedata .normalize ('NFD',texto )if unicodedata .category (c )!='Mn')

    def sin_tildes (texto ):
        return ''.join (c for c in unicodedata .normalize ('NFD',str (texto ).lower ())if unicodedata .category (c )!='Mn')

    _TRAT_SINONIMOS =[
    (['monorelleno','relleno sanitario','disposicion final','vertedero'],'Vertedero'),
    (['reciclaje','reciclado','open-loop','open loop'],'Reciclaje'),
    (['compostaje','compost'],'Compostaje'),
    (['coincineracion','coincineración','combustion','combustión','incineracion','incineración'],'Combustión'),
    (['digestion anaerobica','digestión anaeróbica','anaerobica','anaeróbica'],'Digestión anaeróbica'),
    (['pretratamiento'],'Pretratamiento'),
    (['aplicacion a suelo','aplicación a suelo'],'Aplicación a suelo'),
    ]

    def normalizar_tratamiento (texto ):
        t = normalizar_tratamiento_residuo(texto)
        return {
            'landfill': 'Vertedero',
            'open-loop': 'Reciclaje',
            'anaerobic digestion': 'Digestión anaeróbica',
            'combustion': 'Combustión',
            're-use': 'Reutilización',
        }.get(t, str(texto or '').strip() if t else '')

    def get_factores_residuos (unicos=True):
        conn2 =get_db ()
        cur2 =conn2 .cursor (cursor_factory =psycopg2 .extras .DictCursor )
        cur2 .execute ("""
            SELECT categoria, unidad, factor, tratamiento,
                   CASE WHEN TRIM(LOWER(COALESCE(nombre_chile,''))) IN ('nan','none','') THEN NULL ELSE nombre_chile END as nombre_chile,
                   COALESCE(anio, 0) as anio
            FROM factores ORDER BY anio DESC, categoria, tratamiento
        """)
        todos =[dict (r )for r in cur2 .fetchall ()]
        conn2 .close ()
        palabras_clave ={
        'residuo','residuos','waste','papel','carton','cartón','plastico','plástico',
        'vidrio','metal','organico','orgánico','wood','glass','batteries','bateria',
        'baterias','weee','clothing','tyres','neumaticos','neumáticos','mineral',
        'organic'
        }
        # Solo categorias asociadas a residuos; excluye combustibles, refrigerantes y otros grupos.
        filtrados =[]
        for f in todos :
            categoria =(f .get ('categoria')or '').strip ()
            nombre_chile =(f .get ('nombre_chile')or '').strip ()
            tratamiento =(f .get ('tratamiento')or '').strip ()
            texto =sin_tildes (" ".join ([categoria ,nombre_chile ,tratamiento ]))
            if tratamiento or nombre_chile or any (p in texto for p in palabras_clave ):
                filtrados .append (f )

                # Un solo registro por categoria: se conserva el primero por el orden DESC por anio.
        seen =set ()
        unique =[]
        for f in filtrados :
            categoria =(f .get ('categoria')or '').strip ()
            if not categoria or categoria in seen :
                continue
            seen .add (categoria )
            unique .append (f )
        return unique if unicos else filtrados

    def buscar_factor (residuo_limpio ,factores_db ,anio =0 ,tratamiento =None ,categoria_defra =None ):
        defra_cat = categoria_defra or clasificar_defra(residuo_limpio)
        seleccionado = resolver_factor_residuo(
            categoria=defra_cat,
            tratamiento=tratamiento,
            anio=anio,
            factores=factores_db,
            unidad='tonne',
        )
        if not seleccionado:
            return (0.0, defra_cat, '', '')
        return (
            float(seleccionado['factor']),
            defra_cat,
            seleccionado.get('nombre_chile', '') or '',
            seleccionado.get('tratamiento', '') or '',
        )

    if request .method =="POST":
        tipo_ingreso =request .form .get ("tipo_ingreso","manual")
        empresa =session .get ('empresa')

        # === PASO 2: enviar a revisión del admin ===
        if tipo_ingreso =='confirmar_pdf':
            datos_json =request .form .get ('datos_confirmados','[]')
            nombre_pdf =request .form .get ('nombre_pdf','desconocido')
            tipo_pdf =request .form .get ('tipo_pdf','pdf')
            periodo_override =request .form .get ('periodo_override','').strip ()# YYYY-MM
            if periodo_override :
                try :
                    year ,month =periodo_override .split ('-')
                    fecha_override =f"{year }-{month }-01"
                    filas =json .loads (datos_json )
                    for f in filas :
                        f ['fecha']=fecha_override 
                    datos_json =json .dumps (filas )
                except Exception :
                    pass 
            try :
                conn =get_db ()
                cursor =conn .cursor ()
                cursor .execute ("""
                    INSERT INTO pending_pdf_uploads (empresa, fecha_subida, nombre_archivo, tipo, datos_json, estado)
                    VALUES (%s, %s, %s, %s, %s, 'pendiente')
                """,(empresa ,datetime .now ().strftime ("%Y-%m-%d %H:%M"),nombre_pdf ,tipo_pdf ,datos_json ))
                conn .commit ()
                conn .close ()
                flash ("Tu archivo fue enviado para revisión. El administrador lo validará y recibirás confirmación pronto.","info")
            except Exception as e :
                flash (f"Error al enviar: {str (e )}","danger")
            return redirect ("/residuos")

            # === PASO 1: procesar PDF y mostrar preview ===
        if tipo_ingreso in ['sinader','sidrep']:
            archivo_pdf =request .files .get ("archivo_pdf")
            if not archivo_pdf or archivo_pdf .filename =='':
                flash ("Debes subir un archivo PDF válido.","danger")
                return redirect (request .url )
            try :
                temp_path =os .path .join (tempfile .gettempdir (),archivo_pdf .filename )
                archivo_pdf .save (temp_path )
                if tipo_ingreso =='sinader':
                    df_extraido =extract_sinader_data (temp_path )
                    col_res ,col_trat ,col_cant ,col_dest ='Residuo','Tipo Tratamiento','Cantidad (kg)','Destino'
                else :
                    df_extraido =extract_sidrep_data (temp_path )
                    col_res ,col_trat ,col_cant ,col_dest ='Descripción Residuo','Estado del Residuo','Cantidad (Kg)','Empresa destinataria'
                os .remove (temp_path )

                conn =get_db ()
                cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
                cursor .execute ("""
                    SELECT categoria, factor, nombre_chile, tratamiento,
                           COALESCE(anio, 0) as anio, unidad
                    FROM factores
                """)
                factores_db =cursor .fetchall ()
                conn .close ()

                # Extraer anio y mes del PDF para asignar fecha correcta a los registros
                anio_pdf =datetime .now ().year 
                mes_pdf =None # None = desconocido
                periodo_detectado =""# YYYY-MM para el input type=month
                try :
                    if 'Periodo'in df_extraido .columns and len (df_extraido )>0 :
                    # SINADER: Periodo tiene formato MM/YYYY
                        periodo_str =str (df_extraido ['Periodo'].iloc [0 ])
                        m_anio =re .search (r'\b(20\d{2})\b',periodo_str )
                        m_mes =re .match (r'^(\d{2})/',periodo_str .strip ())
                        if m_anio :
                            anio_pdf =int (m_anio .group (1 ))
                        if m_mes :
                            mes_pdf =int (m_mes .group (1 ))
                    elif 'FechaDeclaración'in df_extraido .columns and len (df_extraido )>0 :
                    # SIDREP: FechaDeclaración tiene formato YYYY-MM-DD
                        fecha_str =str (df_extraido ['FechaDeclaración'].iloc [0 ])
                        m_fecha =re .match (r'^(\d{4})-(\d{2})',fecha_str )
                        if m_fecha :
                            anio_pdf =int (m_fecha .group (1 ))
                            mes_pdf =int (m_fecha .group (2 ))
                except Exception :
                    pass 

                if mes_pdf :
                    fecha_registro_pdf =f"{anio_pdf }-{mes_pdf :02d}-01"
                    periodo_detectado =f"{anio_pdf }-{mes_pdf :02d}"
                else :
                    fecha_registro_pdf =f"{anio_pdf }-01-01"
                    periodo_detectado =f"{anio_pdf }-01"

                preview_rows =[]
                for _ ,row in df_extraido .iterrows ():
                    residuo_raw =str (row .get (col_res ,'Desconocido'))
                    destino =str (row .get (col_dest ,''))
                    residuo_limpio =re .sub (r'^[\d\s]+','',residuo_raw ).strip ()

                    raw_cant =row .get (col_cant ,0 )
                    cantidad =0.0 
                    if pd .isna (raw_cant ):cantidad =0.0 
                    elif isinstance (raw_cant ,(int ,float )):cantidad =float (raw_cant )
                    else :
                        txt =str (raw_cant ).lower ().replace ('kg','').strip ()
                        if '.'in txt and ','in txt :txt =txt .replace ('.','').replace (',','.')
                        elif ','in txt :txt =txt .replace (',','.')
                        elif '.'in txt and len (txt .split ('.')[-1 ])==3 :txt =txt .replace ('.','')
                        try :cantidad =float (txt )
                        except :cantidad =0.0 

                    if cantidad <=0 :continue 

                    categoria_defra = (
                        row.get('DEFRA_Residuo')
                        or row.get('DEFRA_English')
                        or None
                    )
                    trat_pdf = normalizar_tratamiento(
                        str(row.get('DEFRA_Tratamiento') or row.get(col_trat, ''))
                    )
                    factor ,defra_cat ,nombre_chile ,tratamiento_defra =buscar_factor (
                    residuo_limpio ,factores_db ,anio =anio_pdf ,tratamiento =trat_pdf ,
                    categoria_defra=categoria_defra )
                    if not trat_pdf :
                        trat_pdf =tratamiento_defra or ''
                    emision =calcular_emision_residuo(cantidad, factor) or 0.0
                    preview_rows .append ({
                    'fecha':fecha_registro_pdf ,
                    'destino':(destino or 'Operaciones')[:50 ],
                    'categoria':residuo_limpio ,
                    'codigo_residuo':str(row.get('Código principal') or '').strip(),
                    'nombre_chile':nombre_chile ,
                    'tratamiento':trat_pdf ,
                    'cantidad':cantidad ,
                    'factor':factor ,
                    'emision':emision ,
                    'defra_cat':defra_cat ,
                    'tratamiento_defra':normalizar_tratamiento_residuo(trat_pdf),
                    'estado_validacion':'calculado' if factor > 0 else 'requiere_revision',
                    'observacion':'' if factor > 0 else 'No existe coincidencia exacta de factor.',
                    })

                conn2 =get_db ()
                cur2 =conn2 .cursor ()
                cur2 .execute ("SELECT DISTINCT tratamiento FROM factores WHERE tratamiento IS NOT NULL AND tratamiento != '' ORDER BY tratamiento")
                tratamientos_disponibles =[r [0 ]for r in cur2 .fetchall ()]
                conn2 .close ()

                return render_template ("formulario_residuos.html",
                factores =get_factores_residuos (),
                factores_catalogo =get_factores_residuos (unicos=False),
                preview_rows =preview_rows ,
                preview_json =json .dumps (preview_rows ),
                tipo_ingreso_prev =tipo_ingreso ,
                nombre_pdf =archivo_pdf .filename ,
                periodo_detectado =periodo_detectado ,
                tratamientos_disponibles =tratamientos_disponibles )
            except Exception as e :
                flash (f"Error procesando el PDF. Detalle: {str (e )}","danger")
                return redirect (request .url )

                # === MANUAL ===
        conn =get_db ()
        cursor =conn .cursor ()
        periodos =request .form .getlist ("periodo[]")
        tipos =request .form .getlist ("tipo_residuo[]")
        cantidades =request .form .getlist ("cantidad[]")
        tratamientos =request .form .getlist ("tratamiento[]")
        factores_filas =request .form .getlist ("factor[]")
        destinos =request .form .getlist ("destino[]")

        cursor .execute ("""
            SELECT categoria, COALESCE(tratamiento, '') as tratamiento,
                   COALESCE(anio, 0) as anio, unidad, factor
            FROM factores
        """)
        factores_catalogo = [
            {
                'categoria':row[0],
                'tratamiento':row[1],
                'anio':row[2],
                'unidad':row[3],
                'factor':row[4],
            }
            for row in cursor.fetchall()
        ]

        filas_guardadas =0 
        if len(cantidades) != len(periodos) or len(tipos) != len(periodos):
            conn.close()
            flash('La tabla de residuos está incompleta. Revisa las filas antes de guardar.','error')
            return redirect('/formulario_residuos')
        for i in range (len (periodos )):
            if not periodos [i ].strip ()or not cantidades [i ].strip ():continue 
            fecha_limpia =f"{periodos [i ]}-01"
            try :
                cant =numero_finito(cantidades[i], 'La cantidad de residuos')
                fecha_limpia =fecha_periodo(periodos[i]).isoformat()
                anio_periodo =int(periodos[i][:4])
                trat_raw =normalizar_tratamiento (tratamientos [i ]if i <len (tratamientos )else '')
                seleccionado =resolver_factor_residuo(
                    categoria=tipos[i],
                    tratamiento=trat_raw,
                    anio=anio_periodo,
                    factores=factores_catalogo,
                    unidad='tonne',
                )
                fac =float(seleccionado['factor']) if seleccionado else 0.0
            except (ValueError,TypeError,IndexError) as exc:
                conn.rollback()
                conn.close()
                flash(str(exc),'error')
                return redirect('/formulario_residuos')
            emision =calcular_emision_residuo(cant, fac) or 0.0
            destino =destinos [i ]if i <len (destinos )else ''
            cursor .execute ("""
                INSERT INTO registros (
                    fecha, empresa, area, alcance, fuente, categoria, actividad,
                    unidad, cantidad, factor, emision, residuo_oficial,
                    tratamiento_normalizado, categoria_defra, tratamiento_defra,
                    estado_validacion, observacion_factor
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,(fecha_limpia ,empresa ,(destino or 'Operaciones')[:50 ],'Alcance 3','Residuos',
            tipos [i ],trat_raw ,'kg',cant ,fac ,emision ,tipos[i],
            normalizar_tratamiento_residuo(trat_raw),tipos[i],normalizar_tratamiento_residuo(trat_raw),
            'calculado' if fac > 0 else 'requiere_revision',
            '' if fac > 0 else 'No existe coincidencia exacta de factor.' ))
            filas_guardadas +=1 

        conn .commit ()
        conn .close ()
        flash (f"Se han guardado {filas_guardadas } registro(s) manuales exitosamente.","success")
        return redirect ("/residuos")

    return render_template (
        "formulario_residuos.html",
        factores=get_factores_residuos(),
        factores_catalogo=get_factores_residuos(unicos=False),
    )

    # ================= REGISTRO MANUAL GENERAL =================
def _parse_float_input (value ,default =0.0 ):
    try :
        return float (str (value ).replace (',','.'))
    except :
        return default 


def _meses_entre (mes_inicio ,mes_fin ):
    inicio =datetime .strptime (f"{mes_inicio }-01","%Y-%m-%d")
    fin =datetime .strptime (f"{mes_fin }-01","%Y-%m-%d")
    if inicio >fin :
        raise ValueError ("El mes de inicio debe ser anterior o igual al mes final.")

    meses =[]
    actual =inicio 
    while actual <=fin :
        meses .append (actual .strftime ("%Y-%m"))
        if actual .month ==12 :
            actual =actual .replace (year =actual .year +1 ,month =1 )
        else :
            actual =actual .replace (month =actual .month +1 )
    return meses 


def _resolver_datos_alcance1 (form, cursor, anio):
    categoria =form .get ('combustible')
    unidad =form .get ('unidad')
    actividad ="Consumo general"

    if categoria in (None ,''):
        raise ValueError('Selecciona una categoría.')
    if not unidad:
        raise ValueError('Selecciona una unidad.')
    if categoria =='Otros':
        categoria =form .get ('otro_combustible')
        if not categoria or not categoria.strip():
            raise ValueError('Completa el nombre de la categoría manual.')
        factor =numero_finito(form.get('otro_factor'), 'El factor manual', positivo=True)
    else :
        factor =resolver_factor_catalogo(cursor,categoria,unidad,anio)

    return categoria ,unidad ,actividad ,factor 


def _obtener_sede_activa_predeterminada (cursor ,empresa ):
    cursor .execute (
    "SELECT id, nombre_sede FROM agua_sedes WHERE empresa = %s AND activo = TRUE ORDER BY nombre_sede ASC, id ASC LIMIT 1",
    (empresa ,),
    )
    return cursor .fetchone ()


def _guardar_flujo_agua (
cursor ,
empresa ,
sede_id ,
periodo_agua ,
tipo_flujo ,
fuente_agua ,
destino_agua ,
volumen_agua ,
proceso_o_area ,
retorna_mismo ,
tiene_tratamiento ,
calidad_dato ,
evidencia ,
observaciones ,
origen_hidrico=None,
):
    volumen_agua =validar_nuevo_flujo(cursor,empresa,sede_id,periodo_agua,tipo_flujo,volumen_agua)
    cursor .execute (
    """
        INSERT INTO agua_flujos
        (empresa, sede_id, periodo, tipo_flujo, fuente_agua, destino_agua, volumen_m3,
         proceso_o_area, retorna_mismo_sistema_hidrico, tiene_tratamiento, calidad_dato,
         evidencia, observaciones, origen_hidrico, fecha_registro)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """,
    (
    empresa ,
    sede_id ,
    periodo_agua ,
    tipo_flujo ,
    fuente_agua ,
    destino_agua ,
    volumen_agua ,
    proceso_o_area ,
    retorna_mismo ,
    tiene_tratamiento ,
    calidad_dato ,
    evidencia ,
    observaciones ,
    origen_hidrico,
    ),
    )


def _empresa_tiene_mezcla_electrica (cursor ,empresa ):
    cursor .execute (
    """
        SELECT
            COUNT (DISTINCT CASE
                WHEN origen_energia = 'Convencional' THEN 'Convencional'
                WHEN origen_energia = 'ERNC' THEN 'ERNC'
                ELSE NULL
            END) AS total_origenes
        FROM registros
        WHERE empresa = %s
          AND fuente = 'Electricidad'
    """,
    (empresa ,)
    )
    res =cursor .fetchone ()
    return int (res [0 ]or 0 )>=2 


def _calcular_factores_electricos (cursor ,empresa ,sistema ,anio_reg ,mes_reg ,origen ,tiene_irec ):
    if origen not in ('Convencional','ERNC') or tiene_irec not in ('Si','No'):
        raise ValueError('El origen de energía o la declaración IREC no son válidos.')
    if tiene_irec == 'Si' and origen != 'ERNC':
        raise ValueError('La declaración IREC requiere un origen ERNC.')
    # Usa el último factor disponible anterior o igual al período informado.
    # Esto permite registrar períodos recientes mientras se publica el factor del año actual.
    cursor .execute (
    """
        SELECT factor_emision_avg
        FROM factores_electricos
        WHERE sistema = %s
          AND (anio < %s OR (anio = %s AND mes <= %s))
        ORDER BY anio DESC, mes DESC
        LIMIT 1
    """,
    (sistema ,anio_reg ,anio_reg ,mes_reg )
    )
    res_ub =cursor .fetchone ()
    if not res_ub or res_ub[0] is None:
        raise ValueError('No existe un factor eléctrico del sistema y año para ese período. Revisa el catálogo.')
    factor_ubicacion =numero_finito(res_ub[0], 'El factor eléctrico')

    mezcla_electrica =_empresa_tiene_mezcla_electrica (cursor ,empresa )

    if origen =='ERNC'and tiene_irec =='Si':
        factor_mercado =0.0 
    elif mezcla_electrica :
        cursor .execute (
        """
            SELECT factor_emision_avg
            FROM factores_electricos
            WHERE LOWER(sistema) = 'residual'
              AND (anio < %s OR (anio = %s AND mes <= %s))
            ORDER BY anio DESC, mes DESC
            LIMIT 1
        """,
        (anio_reg ,anio_reg ,mes_reg )
        )
        res_merc =cursor .fetchone ()
        if not res_merc or res_merc[0] is None:
            raise ValueError('Falta el factor residual del año y período para calcular las emisiones de mercado.')
        factor_mercado =numero_finito(res_merc[0], 'El factor residual')
    else :
        factor_mercado =factor_ubicacion 

    return factor_ubicacion ,factor_mercado 


def _leer_factores_electricos(archivo):
    """Lee y valida factores eléctricos desde el Excel oficial o su CSV equivalente."""
    nombre = str(getattr(archivo, 'filename', '') or getattr(archivo, 'name', '') or '').lower()
    if nombre.endswith('.csv'):
        df = pd.read_csv(archivo, sep=None, engine='python', encoding='utf-8-sig')
        requeridas = {'anio', 'mes', 'sistema', 'factor_emision_avg'}
        faltantes = requeridas.difference({str(c).strip().lower() for c in df.columns})
        if faltantes:
            raise ValueError(f"El CSV no contiene las columnas requeridas: {', '.join(sorted(faltantes))}.")
        renombres = {c: str(c).strip().lower() for c in df.columns}
        df = df.rename(columns=renombres)
    else:
        df = pd.read_excel(archivo, sheet_name='Factores eléctricos', header=0, engine='openpyxl')

    filas = []
    errores = []
    claves = set()
    for idx, row in df.iterrows():
        valores = [row.get(c) for c in ('anio', 'mes', 'sistema', 'factor_emision_avg')]
        if all(pd.isna(v) or str(v).strip() == '' for v in valores):
            continue
        try:
            anio = int(float(row['anio']))
            mes = int(float(row['mes']))
            sistema = str(row['sistema']).strip()
            factor_raw = str(row['factor_emision_avg']).strip().replace(',', '.')
            factor = float(factor_raw)
            if not 1900 <= anio <= 2100:
                raise ValueError('año fuera de rango')
            if not 1 <= mes <= 12:
                raise ValueError('mes fuera de rango')
            if not sistema:
                raise ValueError('sistema vacío')
            if not math.isfinite(factor) or factor < 0:
                raise ValueError('factor no finito o negativo')
            clave = (anio, mes, sistema.casefold())
            if clave in claves:
                raise ValueError('clave año/mes/sistema duplicada')
            claves.add(clave)
            filas.append({'anio': anio, 'mes': mes, 'sistema': sistema, 'factor': factor})
        except (KeyError, TypeError, ValueError) as exc:
            errores.append(f"Fila {idx + 2}: {exc}")
    if errores:
        raise ValueError('Archivo de factores eléctricos inválido. ' + ' | '.join(errores[:5]))
    if not filas:
        raise ValueError('El archivo no contiene factores eléctricos válidos.')
    return filas


@app .route ('/registro',methods =['GET','POST'])
def registro ():
    if 'user_id'not in session :
        return redirect ("/")

    if request .method =='POST':
        empresa =session .get ('empresa')
        alcance =request .form .get ('alcance_oculto','Alcance 1')
        modo_registro =request .form .get ('modo_registro','individual')

        fecha_raw =request .form .get ('fecha')or ''
        if len (fecha_raw )==7 :
            fecha =f"{fecha_raw }-01"
        else :
            fecha =fecha_raw 

        area =request .form .get ('area')
        fuente =request .form .get ('fuente')
        nivel_tension =request .form .get ('nivel_tension') or None

        cantidad =_parse_float_input (request .form .get ('cantidad','0'))

        if alcance in ('Alcance 1','Alcance 2') and modo_registro == 'individual':
            try:
                cantidad =numero_finito(request.form.get('cantidad'), 'La cantidad')
                fecha =fecha_periodo(fecha_raw[:7]).isoformat()
            except ValueError as exc:
                flash(str(exc),'error')
                return redirect(url_for('registro'))

        if alcance == 'Alcance 2' and request.form.get('tiene_irec') == 'Si':
            certificado =request.files.get('certificado_irec')
            valido =False
            if certificado and certificado.filename and certificado.filename.lower().endswith('.pdf'):
                valido =certificado.read(5) == b'%PDF-'
                certificado.seek(0)
            if not valido:
                flash('Adjunta el certificado IREC en formato PDF para declarar energía certificada.','error')
                return redirect(url_for('registro'))

        conn =get_db ()
        cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )

        if alcance == 'Agua':
            if request.form.get('solo_diagnostico_agua') == '1':
                try:
                    _guardar_estado_medicion_agua(cursor,empresa,request.form.get('medicion_entrada_agua'),request.form.get('medicion_salida_agua'),None)
                    conn.commit()
                    return ('',204)
                except ValueError as exc:
                    conn.rollback()
                    return jsonify({'error':str(exc)}),400
                except Exception:
                    conn.rollback()
                    app.logger.exception('No se pudo guardar el diagnóstico de agua')
                    return jsonify({'error':'No se pudo guardar el diagnóstico.'}),500
                finally:
                    conn.close()
            try:
                estado,mensaje = leer_diagnostico(cursor,empresa)
                if estado not in ('ambos','solo_entrada'):
                    raise ValueError(mensaje)
                tipo = request.form.get('tipo_flujo','captacion')
                modo = request.form.get('modo_registro','individual')
                if modo not in ('individual','multiple'):
                    raise ValueError('El modo de registro de agua no es válido.')
                sede = _obtener_sede_activa_predeterminada(cursor,empresa)
                sede_raw = request.form.get('sede_id')
                sede_id = int(sede_raw) if sede_raw else sede['id'] if sede else None
                if modo == 'multiple':
                    inicio,fin = request.form.get('fecha_inicio'),request.form.get('fecha_fin')
                    fecha_periodo(inicio)
                    fecha_periodo(fin)
                    meses = _meses_entre(inicio,fin)
                    cantidades = request.form.getlist('batch_cantidad[]')
                    if len(meses) != len(cantidades):
                        raise ValueError('La tabla no coincide con los meses seleccionados.')
                else:
                    meses = [request.form.get('periodo')]
                    cantidades = [request.form.get('volumen_m3')]
                def respuesta_booleana(nombre):
                    valor = request.form.get(nombre,'no_informado')
                    if valor not in ('si','no','no_informado'):
                        raise ValueError('La información de retorno o tratamiento no es válida.')
                    return None if valor=='no_informado' else valor=='si'
                for mes,cantidad in zip(meses,cantidades):
                    _guardar_flujo_agua(cursor,empresa,sede_id,fecha_periodo(mes),tipo,
                        request.form.get('fuente_agua') if tipo=='captacion' else None,
                        request.form.get('destino_agua') if tipo!='captacion' else None,
                        cantidad,request.form.get('proceso_o_area'),respuesta_booleana('retorna_mismo_sistema_hidrico'),
                        respuesta_booleana('tiene_tratamiento'),request.form.get('calidad_dato'),
                        request.form.get('evidencia'),request.form.get('observaciones'),
                        request.form.get('origen_hidrico') if tipo=='captacion' else None)
                conn.commit()
                flash(f'Se guardaron {len(meses)} registro(s) de agua.','success')
                return redirect(url_for('agua_dashboard'))
            except (ValueError,HuellaAguaError) as exc:
                conn.rollback()
                flash(str(exc),'error')
                return redirect(url_for('registro',tab='agua'))
            except Exception:
                conn.rollback()
                app.logger.exception('No se pudo guardar el inventario de agua')
                flash('No se pudo guardar el agua. No se guardaron cambios.','error')
                return redirect(url_for('registro',tab='agua'))
            finally:
                conn.close()

        if alcance =='Alcance 1'and modo_registro =='multiple':
            fecha_inicio =request .form .get ('fecha_inicio')
            fecha_fin =request .form .get ('fecha_fin')
            cantidades_lote =request .form .getlist ('batch_cantidad[]')

            if not fecha_inicio or not fecha_fin :
                conn .close ()
                flash ("Debes seleccionar un mes de inicio y un mes de final.","error")
                return redirect ("/registro")

            try :
                meses_lote =_meses_entre (fecha_inicio ,fecha_fin )
            except ValueError as exc :
                conn .close ()
                flash (str (exc ),"error")
                return redirect ("/registro")

            if not area :
                conn .close ()
                flash ("Completa el área o instalación antes de guardar el lote.","error")
                return redirect ("/registro")

            if len (cantidades_lote )!=len (meses_lote ):
                conn .close ()
                flash ("La tabla generada no coincide con el rango seleccionado.","error")
                return redirect ("/registro")

            if not request.form.get('combustible') or not request.form.get('unidad'):
                conn .close ()
                flash ("Completa la categoría y la unidad antes de guardar el lote.","error")
                return redirect ("/registro")

            try :
                for idx ,mes in enumerate (meses_lote ):
                    cantidad_raw =str (cantidades_lote [idx ]).strip ()
                    if not cantidad_raw :
                        raise ValueError (f"Falta completar la cantidad del mes {mes }.")
                    cantidad_lote =numero_finito(cantidad_raw, f'La cantidad de {mes}')
                    categoria,unidad,actividad,factor =_resolver_datos_alcance1(request.form,cursor,int(mes[:4]))
                    fecha_lote =f"{mes }-01"
                    emision_lote =round (cantidad_lote *factor ,4 )
                    cursor .execute ("""
                        INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """,(fecha_lote ,empresa ,area ,alcance ,fuente ,categoria ,actividad ,unidad ,cantidad_lote ,factor ,emision_lote ))
                    registro_id =cursor .fetchone ()[0 ]
                    if _es_fuente_combustible_suministro (fuente ):
                        calcular_huella_suministro_registro (cursor ,registro_id )
                conn .commit ()
            except Exception as exc :
                conn .rollback ()
                conn .close ()
                flash (f"No se pudo guardar el lote: {exc }","error")
                return redirect ("/registro")

            conn .close ()
            flash (f"Se guardaron {len (meses_lote )} registros entre {fecha_inicio } y {fecha_fin }.","success")
            return redirect ("/dashboard")

        if alcance =='Alcance 2'and modo_registro =='multiple':
            fecha_inicio =request .form .get ('fecha_inicio')
            fecha_fin =request .form .get ('fecha_fin')
            cantidades_lote =request .form .getlist ('batch_cantidad[]')
            origen =request .form .get ('origen_energia')
            sistema =request .form .get ('sistema_elec')
            nivel_tension =request .form .get ('nivel_tension')
            tiene_irec =request .form .get ('tiene_irec','No')

            if not fecha_inicio or not fecha_fin :
                conn .close ()
                flash ("Debes seleccionar un mes de inicio y un mes de final.","error")
                return redirect ("/registro")

            try :
                meses_lote =_meses_entre (fecha_inicio ,fecha_fin )
            except ValueError as exc :
                conn .close ()
                flash (str (exc ),"error")
                return redirect ("/registro")

            if not area :
                conn .close ()
                flash ("Completa la instalación o sucursal antes de guardar el lote.","error")
                return redirect ("/registro")

            if not origen or not sistema or nivel_tension not in ('alta', 'media', 'baja'):
                conn .close ()
                flash ("Completa el origen de energía y el sistema antes de guardar el lote.","error")
                return redirect ("/registro")

            if len (cantidades_lote )!=len (meses_lote ):
                conn .close ()
                flash ("La tabla generada no coincide con el rango seleccionado.","error")
                return redirect ("/registro")

            categoria =f"Electricidad {sistema }"
            unidad ="kWh"
            actividad ="Consumo de red eléctrica"

            archivo_irec =request .files .get ('certificado_irec')
            if tiene_irec =='Si'and archivo_irec and archivo_irec .filename :
                fecha_consumo_irec =f"{meses_lote [0 ]}-01"
                cursor .execute (
                "INSERT INTO irec_certificados (empresa, fecha_consumo, filename, contenido, fecha_subida) VALUES (%s, %s, %s, %s, %s)",
                (empresa ,fecha_consumo_irec ,archivo_irec .filename ,psycopg2 .Binary (archivo_irec .read ()),datetime .now ().strftime ("%Y-%m-%d %H:%M"))
                )

            try :
                for idx ,mes in enumerate (meses_lote ):
                    cantidad_raw =str (cantidades_lote [idx ]).strip ()
                    if not cantidad_raw :
                        raise ValueError (f"Falta completar la cantidad del mes {mes }.")
                    cantidad_lote =numero_finito(cantidad_raw, f'La cantidad de {mes}')
                    fecha_lote =f"{mes }-01"
                    fecha_dt =datetime .strptime (fecha_lote ,"%Y-%m-%d")
                    anio_reg ,mes_reg =fecha_dt .year ,fecha_dt .month 
                    factor_ubicacion ,factor =_calcular_factores_electricos (cursor ,empresa ,sistema ,anio_reg ,mes_reg ,origen ,tiene_irec )
                    emision_ubicacion =round (cantidad_lote *factor_ubicacion ,4 )
                    emision =round (cantidad_lote *factor ,4 )
                    cursor .execute ("""
                        INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision, emision_ubicacion, origen_energia, tiene_irec, sistema, nivel_tension)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """,(fecha_lote ,empresa ,area ,alcance ,fuente ,categoria ,actividad ,unidad ,cantidad_lote ,factor ,emision ,emision_ubicacion ,origen ,tiene_irec ,sistema ,nivel_tension ))
                    calcular_huella_suministro_registro (cursor ,cursor .fetchone ()[0 ])
                conn .commit ()
            except Exception as exc :
                conn .rollback ()
                conn .close ()
                flash (f"No se pudo guardar el lote eléctrico: {exc }","error")
                return redirect ("/registro")

            conn .close ()
            flash (f"Se guardaron {len (meses_lote )} consumos eléctricos entre {fecha_inicio } y {fecha_fin }.","success")
            return redirect ("/electricidad")

        if alcance =='Alcance 2':
            origen =request .form .get ('origen_energia')
            sistema =request .form .get ('sistema_elec')
            nivel_tension =request .form .get ('nivel_tension')
            tiene_irec =request .form .get ('tiene_irec','No')

            if nivel_tension not in ('alta', 'media', 'baja'):
                conn.close()
                flash ("Selecciona el nivel de tension electrica.", "error")
                return redirect (url_for("registro"))

            categoria =f"Electricidad {sistema }"
            unidad ="kWh"
            actividad ="Consumo de red eléctrica"

            fecha_dt =datetime .strptime (fecha ,"%Y-%m-%d")
            anio_reg ,mes_reg =fecha_dt .year ,fecha_dt .month 

            try:
                factor_ubicacion,factor =_calcular_factores_electricos(cursor,empresa,sistema,anio_reg,mes_reg,origen,tiene_irec)
            except ValueError as exc:
                conn.close()
                flash(str(exc),'error')
                return redirect(url_for('registro'))
            emision_ubicacion =round (cantidad *factor_ubicacion ,4 )

            emision =round (cantidad *factor ,4 )

            # Guardar certificado IREC si fue subido
            archivo_irec =request .files .get ('certificado_irec')
            if tiene_irec =='Si'and archivo_irec and archivo_irec .filename :
                cursor .execute (
                "INSERT INTO irec_certificados (empresa, fecha_consumo, filename, contenido, fecha_subida) VALUES (%s, %s, %s, %s, %s)",
                (empresa ,fecha ,archivo_irec .filename ,psycopg2 .Binary (archivo_irec .read ()),datetime .now ().strftime ("%Y-%m-%d %H:%M"))
                )

        else :
            try:
                categoria,unidad,actividad,factor =_resolver_datos_alcance1(request.form,cursor,int(fecha[:4]))
                if not categoria or not unidad:
                    raise ValueError('Completa la categoría y la unidad.')
            except ValueError as exc:
                conn.close()
                flash(str(exc),'error')
                return redirect(url_for('registro'))

            emision =cantidad *factor 

        if alcance =='Alcance 2':
            cursor .execute ("""
                INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision, emision_ubicacion, origen_energia, tiene_irec, sistema, nivel_tension)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """,(fecha ,empresa ,area ,alcance ,fuente ,categoria ,actividad ,unidad ,cantidad ,factor ,emision ,emision_ubicacion ,origen ,tiene_irec ,sistema ,nivel_tension ))
            calcular_huella_suministro_registro (cursor ,cursor .fetchone ()[0 ])
        else :
            cursor .execute ("""
                INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """,(fecha ,empresa ,area ,alcance ,fuente ,categoria ,actividad ,unidad ,cantidad ,factor ,emision ))
            registro_id =cursor .fetchone ()[0 ]
            if _es_fuente_combustible_suministro (fuente ):
                calcular_huella_suministro_registro (cursor ,registro_id )

        conn .commit ()
        conn .close ()

        if alcance =='Alcance 2':
            flash (f"Consumo eléctrico registrado — Sistema: {sistema } | Factor ubicación: {factor_ubicacion } | Emisión mercado: {emision } kg | Emisión ubicación: {emision_ubicacion } kg","success")
            return redirect ("/electricidad")
        flash ("Registro guardado exitosamente.","success")
        return redirect ("/dashboard")

    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    estado_medicion_agua, mensaje_medicion_agua = _estado_medicion_agua(cursor, session.get('empresa'))
    cursor.execute("SELECT medicion_entrada_agua, medicion_salida_agua FROM configuracion WHERE empresa = %s", (session.get('empresa'),))
    medicion_agua_row = cursor.fetchone()
    cursor .execute ("SELECT categoria, unidad, factor, CASE WHEN TRIM(LOWER(COALESCE(nombre_chile,''))) IN ('nan','none','') THEN NULL ELSE nombre_chile END as nombre_chile, COALESCE(anio, 0) as anio FROM factores ORDER BY anio DESC")
    _all_factores =[dict (row )for row in cursor .fetchall ()]
    conn .close ()
    # Deduplicate: keep latest anio per (categoria, unidad)
    _seen_cu =set ()
    factores_db =[]
    for _f in _all_factores :
        _key =(_f ['categoria'],_f ['unidad'])
        if _key not in _seen_cu :
            _seen_cu .add (_key )
            factores_db .append (_f )

    datos_agrupados ={'combustibles':{},'refrigerantes':{},'residuos':{},'otros':{}}

    for f in factores_db :
        cat =f ['categoria']
        nombre_chile =(f .get ('nombre_chile')or '')
        texto_cat =_normalizar_texto_suministro (f"{cat } {nombre_chile }")

        if 'electricidad'in texto_cat or 'kwh'in _normalizar_texto_suministro (f .get ('unidad')or '')or 'sen'in texto_cat :
            continue 

        grupo =_clasificar_factor_catalogo (cat ,f .get ('unidad') ,nombre_chile )

        if cat not in datos_agrupados [grupo ]:
            datos_agrupados [grupo ][cat ]=[]

        try :
            factor_val =float (f ['factor'])
        except Exception :
            factor_val =0.0
        datos_agrupados [grupo ][cat ].append ({'unidad':f ['unidad'],'factor':factor_val })

    return render_template (
        "registro.html",
        datos_factores=datos_agrupados,
        estado_medicion_agua=estado_medicion_agua,
        mensaje_medicion_agua=mensaje_medicion_agua,
        medicion_entrada_agua=(medicion_agua_row['medicion_entrada_agua'] if medicion_agua_row else None),
        medicion_salida_agua=(medicion_agua_row['medicion_salida_agua'] if medicion_agua_row else None),
    )


    # ================= IMPORTACIÓN Y EXPORTACIÓN =================
def _parse_import_file (file ,factores_dict =None ):
    meses_dict ={
    'enero':'01','febrero':'02','marzo':'03','abril':'04',
    'mayo':'05','junio':'06','julio':'07','agosto':'08',
    'septiembre':'09','octubre':'10','noviembre':'11','diciembre':'12'
    }
    try:
        workbook = pd.ExcelFile(file, engine='openpyxl')
    except Exception:
        raise ValueError('No se pudo leer el archivo Excel. Usa un .xlsx válido basado en la plantilla oficial.') from None
    with workbook:
        if 'Datos Centralizados' not in workbook.sheet_names:
            raise ValueError("El Excel debe contener una pestaña llamada 'Datos Centralizados'.")
        df = pd.read_excel(workbook, sheet_name='Datos Centralizados')
    df.columns = df.columns.map(lambda columna: str(columna).strip())
    if 'Cantidad' not in df.columns:
        raise ValueError("Falta la columna 'Cantidad'. Usa la plantilla oficial de importación.")
    if df.empty:
        raise ValueError('El Excel no contiene registros para importar.')
    df ['Cantidad']=df ['Cantidad'].astype (str ).str .replace (',','.',regex =False )
    df ['Cantidad']=pd .to_numeric (df ['Cantidad'],errors ='coerce')

    col_factor ='Factor emisión (kg CO₂/u)'
    if col_factor in df .columns :
        df [col_factor ]=df [col_factor ].astype (str ).str .replace (',','.',regex =False )
        df [col_factor ]=pd .to_numeric (df [col_factor ],errors ='coerce').fillna (0.0 )
    else :
        df [col_factor ]=0.0 

    filas =[]
    for index ,row in df .iterrows ():
        errores =[]
        advertencias =[]

        def get_texto (columna ,default =''):
            if columna not in row :return default 
            valor =row [columna ]
            if pd .isna (valor )or str (valor ).strip ().lower ()=='nan'or str (valor ).strip ()=='':
                return default 
            return str (valor ).strip ()

            # Saltar fila de descripción (texto largo en Mes) y fila de ejemplo del template
        mes_raw =get_texto ('Mes','')
        if len (mes_raw )>20 :
            continue 
        if (get_texto ('Identificador','')=='GEN-01'and 
        get_texto ('Tipo de Combustible','')=='Diésel'and 
        get_texto ('Sucursal','')=='Sede Central'):
            continue 

        mes_texto =mes_raw .lower ()
        anio_str =get_texto ('Año',str (datetime .now ().year )).replace ('.0','')

        # Validar anio
        try :
            anio_int =int (float (anio_str ))
            if anio_int <2000 or anio_int >2035 :
                errores .append (f"Año fuera de rango: {anio_str }")
        except (ValueError ,TypeError ):
            errores .append (f"Año inválido: '{anio_str }'")
            anio_int =datetime .now ().year 
        anio =str (anio_int )

        if not mes_texto :
            errores .append ("Mes vacío")
        elif mes_texto not in meses_dict :
            errores .append (f"Mes inválido: '{mes_texto }'")
        mes_num =meses_dict .get (mes_texto ,'01')
        fecha_sql =f"{anio }-{mes_num }-01"

        excel_level1 =get_texto ('Level 1','')
        excel_level2 =get_texto ('Level 2','')

        fuente ='Desconocida'
        if excel_level1 .lower ()=='combustibles':
            if 'fija'in excel_level2 .lower ():fuente ='Combustión Fija'
            elif 'móvil'in excel_level2 .lower ()or 'movil'in excel_level2 .lower ():fuente ='Combustible Móvil'
            else :fuente ='Combustión Fija'
        elif excel_level1 .lower ()=='electricidad':fuente ='Electricidad'
        elif excel_level1 .lower ()=='refrigerantes':fuente ='Refrigerantes'
        elif excel_level1 .lower ()=='residuos':fuente ='Residuos'
        elif excel_level1 =='':
            errores .append ("Level 1 vacío")
        else :
            errores .append (f"Level 1 desconocido: '{excel_level1 }'")

        if fuente in ('Combustión Fija','Combustible Móvil','Combustión Estacionaria','Refrigerantes','Fugas de Refrigerantes'):
            alcance ='Alcance 1'
        elif fuente =='Electricidad':
            alcance ='Alcance 2'
        elif fuente =='Residuos':
            alcance ='Alcance 3'
        else :
            alcance =get_texto ('Scope','No definido')
        area =get_texto ('Sucursal','General')
        categoria =get_texto ('Tipo de Combustible','')
        actividad =get_texto ('Tipo Unidad de Consumo','')
        identificador =get_texto ('Identificador','')
        unidad =get_texto ('Unidad de Medida','N/A')

        if fuente =='Electricidad':
            if categoria =='':categoria ='Red Eléctrica'
            if actividad =='':actividad =identificador if identificador !=''else 'Consumo General'

        if categoria =='':categoria ='Desconocida'
        if actividad =='':actividad ='No especificada'

        # Validar cantidad (NaN o vacío se trata como 0 para permitir registros mensuales sin consumo)
        if pd .isna (row ['Cantidad']):
            cantidad =0.0 
            errores.append('La cantidad está vacía o no es numérica; ingresa 0 si no hubo consumo.')
        else :
            cantidad =float (row ['Cantidad'])
            if cantidad <0 :
                errores .append (f"Cantidad negativa ({cantidad })")

                # Factor: columna Excel → auto-lookup en catálogo → 0
        factor_excel =float (row [col_factor ])if not pd .isna (row [col_factor ])else 0.0 
        if factor_excel <0 :
            errores .append (f"Factor negativo ({factor_excel })")
            factor_excel =0.0 

        factor =factor_excel 
        factor_origen ='excel'
        if factor ==0.0 and factores_dict and categoria :
            lookup =factores_dict .get ((categoria .lower (),unidad .lower ()))
            if lookup :
                factor =lookup 
                factor_origen ='auto'
                advertencias .append (f"Factor completado automáticamente desde catálogo ({factor } kg CO₂/{unidad })")

        emision =cantidad *factor 

        filas .append ({
        'row_num':index +2 ,
        'fecha':fecha_sql ,
        'mes':mes_raw ,
        'anio':anio ,
        'fuente':fuente ,
        'alcance':alcance ,
        'area':area ,
        'categoria':categoria ,
        'actividad':actividad ,
        'unidad':unidad ,
        'cantidad':cantidad ,
        'factor':factor ,
        'factor_origen':factor_origen ,
        'sistema':get_texto('Sistema'),
        'origen_energia':get_texto('Origen de Energía','Convencional'),
        'nivel_tension':get_texto('Nivel de Tensión'),
        'tiene_irec':get_texto('IREC','No'),
        'emision':emision ,
        'valid':len (errores )==0 ,
        'error':'; '.join (errores )if errores else None ,
        'advertencia':'; '.join (advertencias )if advertencias else None 
        })

    return filas 


def _preparar_fila_importacion(cursor,empresa,fila):
    """Valida entradas y recalcula los derivados antes de previsualizar o guardar."""
    fecha =fecha_periodo(str(fila.get('fecha',''))[:7])
    cantidad =numero_finito(fila.get('cantidad'), 'La cantidad importada')
    fuente =fila.get('fuente')
    alcances ={'Combustión Fija':'Alcance 1','Combustible Móvil':'Alcance 1','Refrigerantes':'Alcance 1','Residuos':'Alcance 3','Electricidad':'Alcance 2'}
    if fuente not in alcances:
        raise ValueError('La fuente de emisión importada no es válida.')
    datos =dict(fila)
    datos.update(fecha=fecha.isoformat(),cantidad=cantidad,alcance=alcances[fuente])
    if fuente == 'Electricidad':
        sistema =fila.get('sistema')
        if not sistema:
            categoria =str(fila.get('categoria','')).removeprefix('Electricidad ')
            sistema =categoria if categoria in ('SEN','SEA','SEM','SM Los Lagos','Autogeneración') else None
        tension =fila.get('nivel_tension')
        if not sistema or tension not in ('alta','media','baja'):
            raise ValueError('Para importar electricidad agrega Sistema y Nivel de Tensión (alta, media o baja).')
        if fila.get('tiene_irec','No') != 'No':
            raise ValueError('Registra consumos con IREC en el formulario eléctrico y adjunta el certificado.')
        unidad =unidad_canonica(fila.get('unidad'))
        if unidad not in ('kwh','mwh'):
            raise ValueError('La electricidad importada debe expresarse en kWh o MWh.')
        cantidad *= 1000 if unidad == 'mwh' else 1
        ubicacion,mercado =_calcular_factores_electricos(cursor,empresa,sistema,fecha.year,fecha.month,fila.get('origen_energia','Convencional'),'No')
        datos.update(categoria=f'Electricidad {sistema}',cantidad=cantidad,unidad='kWh',
                     sistema=sistema,nivel_tension=tension,tiene_irec='No',
                     origen_energia=fila.get('origen_energia','Convencional'),factor=mercado,
                     emision_ubicacion=round(cantidad*ubicacion,4))
    else:
        datos['factor'] =numero_finito(fila.get('factor'), 'El factor importado')
    datos['emision'] =round(datos['cantidad']*datos['factor'],4)
    return datos


@app .route ("/descargar_plantilla")
def descargar_plantilla ():
    if 'user_id'not in session :
        return redirect ("/")

    columnas =[
    'Mes','Año','Level 1','Level 2','Scope','Sucursal',
    'Tipo de Combustible','Tipo Unidad de Consumo','Identificador',
    'Unidad de Medida','Cantidad','Factor emisión (kg CO₂/u)',
    'Sistema','Nivel de Tensión','Origen de Energía','IREC'
    ]
    descripciones =[
    'Nombre del mes (Enero, Febrero…)','Año (ej: 2025)',
    'Combustibles / Electricidad / Refrigerantes / Residuos',
    'Combustión Fija / Combustión Móvil (solo Combustibles)',
    'Alcance 1 / Alcance 2 / Alcance 3',
    'Nombre de la sucursal o sede',
    'Tipo de combustible o gas refrigerante',
    'Tipo de consumo o proceso',
    'Código o identificador del equipo/medidor',
    'Litros, kWh, kg, etc.',
    'Cantidad consumida (número)',
    'Factor de emisión oficial (kg CO₂ por unidad)',
    'Electricidad: SEN, SEA, SEM o SM Los Lagos',
    'Electricidad: alta, media o baja',
    'Electricidad: Convencional o ERNC',
    'No. Para IREC usa el formulario eléctrico con certificado.'
    ]
    ejemplo =['Enero',2025 ,'Combustibles','Combustión Fija','Alcance 1','Sede Central','Diésel','Generador','GEN-01','Litros',1500.5 ,2.68 ,'','','','No']

    conn =get_db ()
    df_factores =pd .read_sql_query (
    "SELECT categoria as \"Categoría\", unidad as \"Unidad\", factor as \"Factor Oficial\" FROM factores ORDER BY categoria",
    conn 
    )
    conn .close ()

    output =io .BytesIO ()
    with pd .ExcelWriter (output ,engine ='xlsxwriter')as writer :
        workbook =writer .book 

        header_fmt =workbook .add_format ({
        'bold':True ,'bg_color':'#1E40AF','font_color':'#FFFFFF',
        'border':1 ,'text_wrap':True ,'valign':'vcenter','align':'center'
        })
        desc_fmt =workbook .add_format ({
        'italic':True ,'bg_color':'#DBEAFE','font_color':'#1E40AF',
        'border':1 ,'text_wrap':True ,'font_size':9 
        })
        example_fmt =workbook .add_format ({
        'bg_color':'#F0FDF4','font_color':'#166534','border':1 ,'bold':True 
        })
        num_fmt =workbook .add_format ({
        'bg_color':'#F0FDF4','font_color':'#166534','border':1 ,
        'bold':True ,'num_format':'#,##0.00'
        })

        worksheet =workbook .add_worksheet ('Datos Centralizados')
        writer .sheets ['Datos Centralizados']=worksheet 

        col_widths =[12 ,8 ,18 ,22 ,12 ,18 ,24 ,24 ,16 ,16 ,12 ,26 ,18,18,20,16]
        for col ,(name ,width )in enumerate (zip (columnas ,col_widths )):
            worksheet .write (0 ,col ,name ,header_fmt )
            worksheet .set_column (col ,col ,width )
        for col ,desc in enumerate (descripciones ):
            worksheet .write (1 ,col ,desc ,desc_fmt )
        for col ,val in enumerate (ejemplo ):
            fmt =num_fmt if col in (1 ,10 ,11 )else example_fmt 
            worksheet .write (2 ,col ,val ,fmt )

        worksheet .set_row (0 ,30 )
        worksheet .set_row (1 ,42 )
        worksheet .set_row (2 ,20 )
        worksheet .freeze_panes (1 ,0 )

        if not df_factores .empty :
            df_factores .to_excel (writer ,sheet_name ='Catálogo Oficial',index =False )
            ws2 =writer .sheets ['Catálogo Oficial']
            for ci ,col_name in enumerate (df_factores .columns ):
                ws2 .write (0 ,ci ,col_name ,header_fmt )
            ws2 .set_column ('A:A',30 )
            ws2 .set_column ('B:B',15 )
            ws2 .set_column ('C:C',18 )

    output .seek (0 )
    return send_file (output ,download_name ="Plantilla_Masiva_GreenTrack.xlsx",as_attachment =True )


@app .route ("/importar",methods =["GET","POST"])
def importar_registros ():
    if 'user_id'not in session :return redirect ("/")

    if request .method =="POST":
        if 'archivo'not in request .files or request .files ['archivo'].filename =='':
            flash ("No se seleccionó ningún archivo","error")
            return redirect (request .url )

        file =request .files ['archivo']
        if not file or not file.filename.lower().endswith('.xlsx'):
            flash ("Selecciona un archivo .xlsx. Si tienes un .xls, guárdalo como .xlsx antes de cargarlo.","error")
            return redirect (request .url )

        try :
            conn =get_db ()
            cur =conn .cursor ()
            cur .execute ("SELECT categoria, unidad, factor FROM factores")
            factores_dict ={(r [0 ].lower (),r [1 ].lower ()):float (r [2 ])for r in cur .fetchall ()}
            conn .close ()
        except Exception :
            factores_dict ={}

        try :
            filas =_parse_import_file (file ,factores_dict )
            conn =get_db()
            try:
                cur =conn.cursor()
                for fila in filas:
                    if not fila['valid']:
                        continue
                    try:
                        fila.update(_preparar_fila_importacion(cur,session.get('empresa'),fila))
                    except ValueError as exc:
                        fila.update(valid=False,error=str(exc))
            finally:
                conn.close()
        except ValueError as e :
            flash (str (e ),"error")
            return redirect (request .url )
        except Exception as e :
            flash (f"Error técnico al procesar: {str (e )}","error")
            return redirect (request .url )

        total =len (filas )
        validos =sum (1 for f in filas if f ['valid'])
        errores =total -validos 
        total_emision =sum (f ['emision']for f in filas if f ['valid'])

        from collections import defaultdict 
        _fc =defaultdict (lambda :{'count':0 ,'emision':0.0 })
        for f in filas :
            if f ['valid']:
                _fc [f ['fuente']]['count']+=1 
                _fc [f ['fuente']]['emision']+=f ['emision']
        resumen_fuentes =[
        {'fuente':k ,'count':v ['count'],'emision':v ['emision']}
        for k ,v in sorted (_fc .items (),key =lambda x :-x [1 ]['emision'])
        ]

        return render_template ("importar_preview.html",
        filas =filas ,
        filas_json =json .dumps (filas ),
        total =total ,
        validos =validos ,
        errores =errores ,
        total_emision =total_emision ,
        resumen_fuentes =resumen_fuentes 
        )

    return render_template ("importar.html")


@app .route ("/importar/confirmar",methods =["POST"])
def importar_confirmar ():
    if 'user_id'not in session :return redirect ("/")
    empresa =session .get ('empresa')

    filas_json =request .form .get ('filas_json','[]')
    try :
        filas =json .loads (filas_json )
    except Exception :
        flash ("Error al procesar los datos. Intente nuevamente.","error")
        return redirect ("/importar")

    if not isinstance(filas,list) or any(not isinstance(f,dict) for f in filas):
        flash('El contenido de la importación no es válido.','error')
        return redirect('/importar')
    filas_validas =[f for f in filas if f .get ('valid')]
    if not filas_validas :
        flash ("No hay registros válidos para importar.","error")
        return redirect ("/importar")

    try :
        conn =get_db ()
        cursor =conn .cursor ()
        for fila in filas_validas :
            fila.update(_preparar_fila_importacion(cursor,empresa,fila))
            cursor .execute ("""
                INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision,
                    emision_ubicacion, origen_energia, tiene_irec, sistema, nivel_tension)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """,(fila ['fecha'],empresa ,fila ['area'],fila ['alcance'],fila ['fuente'],
            fila ['categoria'],fila ['actividad'],fila ['unidad'],
            fila ['cantidad'],fila ['factor'],fila ['emision'],
            fila.get('emision_ubicacion'),fila.get('origen_energia'),fila.get('tiene_irec'),fila.get('sistema'),fila.get('nivel_tension')))
            registro_id = cursor.fetchone()[0]
            if _es_fuente_combustible_suministro(fila['fuente']) or str(fila['alcance']).lower() == 'alcance 2':
                calcular_huella_suministro_registro(cursor, registro_id)
        conn .commit ()
        conn .close ()

        from collections import defaultdict 
        _fc =defaultdict (lambda :{'count':0 ,'emision':0.0 })
        for f in filas_validas :
            _fc [f ['fuente']]['count']+=1 
            _fc [f ['fuente']]['emision']+=f ['emision']

        session ['import_result']={
        'guardados':len (filas_validas ),
        'total_emision':sum (f ['emision']for f in filas_validas ),
        'por_fuente':[
        {'fuente':k ,'count':v ['count'],'emision':v ['emision']}
        for k ,v in sorted (_fc .items (),key =lambda x :-x [1 ]['emision'])
        ]
        }
        return redirect ("/importar/resultado")

    except Exception as e :
        if 'conn' in locals() and not conn.closed:
            conn.rollback()
            conn.close()
        flash (f"Error al guardar los datos: {str (e )}","error")
        return redirect ("/importar")


@app .route ("/importar/resultado")
def importar_resultado ():
    if 'user_id'not in session :return redirect ("/")
    result =session .pop ('import_result',None )
    if not result :
        return redirect ("/importar")
    return render_template ("importar_resultado.html",
    guardados =result ['guardados'],
    total_emision =result ['total_emision'],
    por_fuente =result ['por_fuente']
    )

def _etiquetar_unidad_factor(df):
    """Expone la base del factor almacenado sin convertir valores históricos."""
    df['Unidad del factor'] = [
        'kg CO₂e/t' if str(fuente).strip() == 'Residuos' else f'kg CO₂e/{unidad}'
        for fuente, unidad in zip(df['fuente'], df['unidad'])
    ]


def _datos_huella_hidrica_exportacion(conn, empresa, anio=''):
    """Construye hojas exportables sin mezclar unidades hídricas con emisiones."""
    periodo = '' if anio in (None, '', 'Todos') else anio
    resumen = crear_resumen_agua(conn, empresa, periodo or None)
    def valor(campo):
        dato = resumen.get(campo)
        return round(float(dato), 6) if dato is not None else None

    df_resumen = pd.DataFrame([
        {'Indicador': 'Captación total', 'Valor': valor('captacion_total'), 'Unidad': 'm³', 'Estado': resumen.get('nivel')},
        {'Indicador': 'Retorno total', 'Valor': valor('retornos_totales'), 'Unidad': 'm³', 'Estado': resumen.get('nivel')},
        {'Indicador': 'Reúso total', 'Valor': valor('reuso'), 'Unidad': 'm³', 'Estado': resumen.get('nivel')},
        {'Indicador': 'Consumo operativo', 'Valor': valor('consumo'), 'Unidad': 'm³', 'Estado': resumen.get('nivel')},
        {'Indicador': 'Huella hídrica de suministro energético', 'Valor': valor('huella_suministro_total'), 'Unidad': 'm³', 'Estado': 'Completa' if resumen.get('suministro_completo') else 'Parcial'},
        {'Indicador': 'Huella hídrica total', 'Valor': valor('huella_hidrica_total'), 'Unidad': 'm³', 'Estado': resumen.get('nivel')},
        {'Indicador': 'Huella de escasez', 'Valor': valor('huella'), 'Unidad': 'm³-eq', 'Estado': resumen.get('nivel')},
        {'Indicador': 'Calidad de datos', 'Valor': resumen.get('calidad'), 'Unidad': '', 'Estado': resumen.get('mensaje_medicion_agua') or 'Sin observaciones'},
    ])

    filas_mensuales = []
    for periodo, datos in sorted(resumen.get('tendencia', {}).items()):
        filas_mensuales.append({
            'Período': periodo,
            'Captación (m³)': datos.get('captacion'),
            'Retorno (m³)': datos.get('retorno'),
            'Reúso (m³)': datos.get('reuso'),
            'Consumo operativo (m³)': datos.get('consumo'),
        })
    df_mensual = pd.DataFrame(filas_mensuales)

    df_flujos = pd.DataFrame(resumen.get('flujos', []))
    if not df_flujos.empty:
        columnas = [c for c in ('periodo', 'sede_id', 'tipo_flujo', 'fuente_agua', 'destino_agua', 'volumen_m3', 'retorna_mismo_sistema_hidrico') if c in df_flujos.columns]
        df_flujos = df_flujos[columnas].rename(columns={
            'periodo': 'Período', 'sede_id': 'Sede', 'tipo_flujo': 'Tipo de flujo',
            'fuente_agua': 'Fuente de agua', 'destino_agua': 'Destino de agua',
            'volumen_m3': 'Volumen (m³)', 'retorna_mismo_sistema_hidrico': 'Retorna al mismo sistema hídrico'
        })
    return df_resumen, df_mensual, df_flujos


@app .route ("/exportar",methods =["GET"])
def exportar ():
    if 'user_id'not in session :return redirect ("/")
    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("SELECT DISTINCT SUBSTRING(fecha::text,1,4) as anio FROM registros WHERE empresa=%s AND fecha IS NOT NULL ORDER BY anio DESC",(empresa ,))
    anios =[r [0 ]for r in cursor .fetchall ()]
    cursor .execute ("SELECT DISTINCT alcance FROM registros WHERE empresa=%s AND alcance IS NOT NULL ORDER BY alcance",(empresa ,))
    alcances =[r [0 ]for r in cursor .fetchall ()]
    cursor .execute ("SELECT DISTINCT fuente FROM registros WHERE empresa=%s AND fuente IS NOT NULL ORDER BY fuente",(empresa ,))
    fuentes =[r [0 ]for r in cursor .fetchall ()]
    anio_seleccionado = _anio_panel(cursor)
    if anio_seleccionado != 'Todos' and anio_seleccionado not in anios:
        anios = sorted(anios + [anio_seleccionado], reverse=True)
    conn .close ()
    return render_template ("exportar.html",empresa =empresa ,anio_seleccionado=anio_seleccionado,anios =anios ,alcances =alcances ,fuentes =fuentes )

@app .route ("/exportar_completo")
def exportar_completo ():
    return exportar_avanzado ()

@app .route ("/exportar_avanzado")
def exportar_avanzado ():
    if 'user_id'not in session :
        return redirect ("/")

    empresa =session .get ('empresa')
    anio =request .args .get ('anio','').strip ()
    alcances_sel =request .args .getlist ('alcance')
    fuentes_sel =request .args .getlist ('fuente')
    fecha_inicio =request .args .get ('fecha_inicio','').strip ()
    fecha_fin =request .args .get ('fecha_fin','').strip ()
    incluir_huella_hidrica = request.args.get('incluir_huella_hidrica') in ('1', 'true', 'on', 'si')

    anio = '' if anio == 'Todos' else anio
    regreso = url_for('exportar', anio=anio, fecha_inicio=fecha_inicio,
                      fecha_fin=fecha_fin, alcance=alcances_sel, fuente=fuentes_sel,
                      incluir_huella_hidrica='1' if incluir_huella_hidrica else None)
    try:
        if anio and (not re.fullmatch(r'\d{4}', anio) or int(anio) < 1):
            raise ValueError('Selecciona un año válido para exportar.')
        fechas = []
        for valor in (fecha_inicio, fecha_fin):
            if valor and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', valor):
                raise ValueError('Ingresa fechas válidas con formato año-mes-día.')
            try:
                fechas.append(datetime.strptime(valor, '%Y-%m-%d').date() if valor else None)
            except ValueError:
                raise ValueError('Ingresa fechas válidas para el rango de exportación.') from None
        if all(fechas) and fechas[0] > fechas[1]:
            raise ValueError('La fecha inicial no puede ser posterior a la fecha final.')
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(regreso)

    conn =get_db ()
    where =["empresa = %s"]
    params =[empresa ]

    if anio :
        where .append ("SUBSTRING(fecha::text,1,4) = %s");params .append (anio )
    if alcances_sel :
        where .append (f"alcance IN %s");params .append (tuple (alcances_sel ))
    if fuentes_sel :
        where .append (f"fuente IN %s");params .append (tuple (fuentes_sel ))
    if fecha_inicio :
        where .append ("fecha >= %s");params .append (fecha_inicio )
    if fecha_fin :
        where .append ("fecha <= %s");params .append (fecha_fin )

    where_sql =" AND ".join (where )
    df =pd .read_sql_query (
    f"SELECT fecha, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision "
    f"FROM registros WHERE {where_sql } ORDER BY fecha DESC, id DESC",
    conn ,params =tuple (params ))
    hojas_agua = _datos_huella_hidrica_exportacion(conn, empresa, anio) if incluir_huella_hidrica else None
    conn .close ()

    if df .empty and not incluir_huella_hidrica:
        flash ("No hay registros que coincidan con los filtros seleccionados.","warning")
        return redirect(regreso)

    if not df.empty:
        _etiquetar_unidad_factor(df)
        df .rename (columns ={
    'fecha':'Fecha','area':'Área / Instalación','alcance':'Alcance',
    'fuente':'Fuente','categoria':'Categoría','actividad':'Actividad',
    'unidad':'Unidad','cantidad':'Cantidad','factor':'Factor de emisión',
        'emision':'Emisión (kg CO₂e)'
        },inplace =True )

    from openpyxl .styles import Font ,PatternFill ,Alignment 
    output =io .BytesIO ()
    with pd .ExcelWriter (output ,engine ='openpyxl')as writer :
        if not df.empty:
            df .to_excel (writer ,sheet_name ='Registros',index =False )
        if hojas_agua:
            hojas_agua[0].to_excel(writer, sheet_name='Huella Hídrica Resumen', index=False)
            if not hojas_agua[1].empty:
                hojas_agua[1].to_excel(writer, sheet_name='Huella Hídrica Mensual', index=False)
            if not hojas_agua[2].empty:
                hojas_agua[2].to_excel(writer, sheet_name='Huella Hídrica Flujos', index=False)
        for ws in writer.sheets.values():
            for cell in ws[1]:
                cell.fill = PatternFill("solid", fgColor="1E40AF")
                cell.font = Font(bold=True, color="FFFFFF")
                cell.alignment = Alignment(horizontal='center')
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = 22
    output .seek (0 )
    sufijo =f"_{anio }"if anio else "_completo"
    nombre =f"Datos_GreenTrack_{empresa .replace (' ','_')}{sufijo }_{datetime .now ().strftime ('%Y%m%d')}.xlsx"
    return send_file (output ,download_name =nombre ,as_attachment =True )


    # ================= RUTAS ADMIN =================
def get_admin_stats ():
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("SELECT COUNT(*) FROM usuarios WHERE es_admin = 0 AND COALESCE(NULLIF(rol,''),'empresa') = 'empresa'")
    total_emp =cursor .fetchone ()[0 ]or 0 
    cursor .execute ("SELECT COUNT(*) FROM registros")
    total_reg =cursor .fetchone ()[0 ]or 0 
    cursor .execute ("SELECT SUM(emision) FROM registros")
    total_em =cursor .fetchone ()[0 ]or 0 
    try :
        cursor .execute ("SELECT COUNT(*) FROM pending_pdf_uploads WHERE estado = 'pendiente'")
        pendientes =cursor .fetchone ()[0 ]or 0 
    except Exception :
        pendientes =0 
    conn .close ()
    return total_emp ,total_reg ,total_em ,pendientes 

def calcular_metricas_agua_admin(cursor,empresas_filtro):
    """Misma medición validada que ve la empresa, sin convertir ausencias en cero."""
    empresas=[e['empresa'] for e in empresas_filtro if e.get('empresa')]
    cursor.execute("""SELECT empresa,SUBSTRING(periodo::text,1,4) AS anio FROM agua_flujos
        UNION SELECT empresa,SUBSTRING(fecha::text,1,4) FROM registros
        WHERE fuente='Electricidad' OR LOWER(fuente) LIKE %s""",('%combusti%',))
    years={}
    for row in cursor.fetchall():
        if row['anio']:
            years.setdefault(row['empresa'],set()).add(row['anio'])
    agua_map,totales={},{}
    for empresa in empresas:
        totales[empresa]=crear_resumen_agua(cursor.connection,empresa)['huella_hidrica_total']
        agua_map[empresa]={anio:crear_resumen_agua(cursor.connection,empresa,anio)['huella_hidrica_total']
            for anio in sorted(years.get(empresa,set()))}
    anios=sorted({y for values in agua_map.values() for y in values},reverse=True)
    charts={'Todos':[totales[e] for e in empresas]}
    charts.update({y:[agua_map[e].get(y) for e in empresas] for y in anios})
    disponibles=[v for v in totales.values() if v is not None]
    total=round(sum(disponibles),2) if disponibles else None
    top=sorted([{'empresa':e,'total':round(v,2)} for e,v in totales.items() if v is not None and v>0],
        key=lambda r:-r['total'])[:6]
    return agua_map,empresas,anios,charts,total,top

@app .route ("/admin/dashboard")
def admin_dashboard ():
    if 'user_id'not in session or session .get ('es_admin')!=1 :return redirect ("/")
    t_emp ,t_reg ,t_em ,t_pend =get_admin_stats ()

    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("SELECT id, empresa, email, contacto, rut, fecha_registro, sector_empresa, tipo_empresa FROM usuarios WHERE es_admin = 0 AND COALESCE(NULLIF(rol,''),'empresa') = 'empresa' ORDER BY fecha_registro DESC LIMIT 5")
    ultimas =[dict (row )for row in cursor .fetchall ()]
    cursor .execute ("SELECT id, empresa, sector_empresa, tipo_empresa FROM usuarios WHERE es_admin = 0 AND COALESCE(NULLIF(rol,''),'empresa') = 'empresa' ORDER BY empresa")
    empresas_filtro =[dict (row )for row in cursor .fetchall ()]

    cursor .execute ("""
        SELECT empresa, SUBSTRING(fecha::text,1,4) AS anio, SUM(emision) AS total
        FROM registros WHERE empresa IS NOT NULL AND fecha IS NOT NULL AND LENGTH(fecha::text) >= 4
        GROUP BY empresa, anio
    """)
    em_map ={}
    for row in cursor .fetchall ():
        emp ,anio_val ,total =row ['empresa'],row ['anio'],float (row ['total']or 0 )
        if emp and anio_val :
            if emp not in em_map :em_map [emp ]={}
            em_map [emp ][anio_val ]=em_map [emp ].get (anio_val ,0 )+total 

    all_empresas =sorted (em_map .keys ())
    all_anios =sorted ({a for d in em_map .values ()for a in d .keys ()},reverse =True )
    chart_data ={"Todos":[round (sum (em_map .get (e ,{}).values ()),2 )for e in all_empresas ]}
    for anio in all_anios :
        chart_data [anio ]=[round (em_map .get (e ,{}).get (anio ,0 ),2 )for e in all_empresas ]

    top_emisores =sorted (
    [{'empresa':e ,'total':round (sum (v .values ()),2 )}for e ,v in em_map .items ()],
    key =lambda x :-x ['total']
    )[:6 ]
    agua_map ,all_agua_empresas ,all_agua_anios ,chart_agua_data ,total_huella_hidrica_global ,top_huella_hidrica =calcular_metricas_agua_admin (cursor ,empresas_filtro )

    medidas_empresas =obtener_medidas_empresas (conn )
    cursor .execute ("""
        SELECT empresa, anio, periodo, unidad, valor
        FROM medidas_productivas
        WHERE periodo = anio::text
        ORDER BY empresa, anio DESC, id DESC
    """)
    medidas_todas =[dict (row )for row in cursor .fetchall ()]
    intensidad_empresas_labels =[emp ['empresa']for emp in empresas_filtro ]
    intensidad_series ={}
    intensidad_latest ={}
    intensidad_huella_series ={}
    intensidad_huella_latest ={}
    for row in medidas_todas :
        emp =row ['empresa']
        anio =str (row ['anio'])
        _ ,intensidad =calcular_intensidad_emisiones (conn ,emp ,row ['anio'],row .get ('valor')or 0 )
        intensidad_series .setdefault (anio ,{})[emp ]=round (float (intensidad or 0 ),6 )
        if emp not in intensidad_latest :
            intensidad_latest [emp ]=round (float (intensidad or 0 ),6 )
        try :
            medida_valor =float (row .get ('valor')or 0 )
        except Exception :
            medida_valor =0 
        huella_anual =agua_map.get(emp,{}).get(anio)
        intensidad_huella =round(huella_anual/medida_valor,6) if huella_anual is not None and medida_valor>0 else None
        intensidad_huella_series .setdefault (anio ,{})[emp ]=intensidad_huella 
        if emp not in intensidad_huella_latest :
            intensidad_huella_latest [emp ]=intensidad_huella 
    intensidad_series ['Todos']=intensidad_latest 
    intensidad_huella_series ['Todos']=intensidad_huella_latest 
    intensidad_empresas_values =[round (float (intensidad_latest .get (emp ['empresa'])or 0 ),6 )for emp in empresas_filtro ]
    intensidad_huella_values =[intensidad_huella_latest.get(emp['empresa']) for emp in empresas_filtro]
    intensidad_series_values ={
    key :[round (float ((serie .get (emp ['empresa']))or 0 ),6 )for emp in empresas_filtro ]
    for key ,serie in intensidad_series .items ()
    }
    intensidad_huella_series_values ={
    key :[serie.get(emp['empresa']) for emp in empresas_filtro]
    for key ,serie in intensidad_huella_series .items ()
    }
    intensidad_years =sorted ([k for k in intensidad_series .keys ()if k !='Todos'],reverse =True )
    intensidades_validas=[v for v in intensidad_huella_values if v is not None]
    unidades_validas={medidas_empresas.get(emp['empresa'],{}).get('unidad') for emp in empresas_filtro if intensidad_huella_latest.get(emp['empresa']) is not None}
    intensidad_huella_promedio=round(sum(intensidades_validas)/len(intensidades_validas),6) if intensidades_validas and len(unidades_validas)==1 else None
    conn .close ()

    return render_template ("admin.html",
    total_empresas =t_emp ,total_registros =t_reg ,total_emisiones =t_em ,total_pendientes =t_pend ,
    total_huella_hidrica_global =total_huella_hidrica_global ,intensidad_huella_promedio =intensidad_huella_promedio ,
    ultimas_empresas =ultimas ,empresas =empresas_filtro ,admin_section ="dashboard",
    chart_empresas =all_empresas ,chart_data =chart_data ,chart_anios =all_anios ,
    top_emisores =top_emisores ,medidas_empresas =medidas_empresas ,
    chart_agua_empresas =all_agua_empresas ,chart_agua_data =chart_agua_data ,chart_agua_anios =all_agua_anios ,
    top_huella_hidrica =top_huella_hidrica ,
    chart_intensidad_empresas =intensidad_empresas_labels ,chart_intensidad_values =intensidad_empresas_values ,
    chart_intensidad_series =intensidad_series_values ,chart_intensidad_years =intensidad_years ,
    chart_intensidad_huella_values =intensidad_huella_values ,
    chart_intensidad_huella_series =intensidad_huella_series_values ,
    sectores_empresa =SECTORES_EMPRESA ,unidades_productivas =UNIDADES_PRODUCTIVAS ,
    current_year =datetime .now ().year )

@app .route ("/admin/empresas")
def admin_empresas ():
    if not can_manage_companies ():return redirect ("/")
    t_emp ,t_reg ,t_em ,t_pend =get_admin_stats ()
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("SELECT id, empresa, email, contacto, rut, fecha_registro, sector_empresa, tipo_empresa, es_demo FROM usuarios WHERE es_admin = 0 AND COALESCE(NULLIF(rol,''),'empresa') = 'empresa' ORDER BY empresa")
    empresas =[dict (row )for row in cursor .fetchall ()]
    cursor .execute ("SELECT empresa, COUNT(*) as cnt, SUM(emision) as total FROM registros GROUP BY empresa")
    em_stats ={}
    for r in cursor .fetchall ():
        if r ['empresa']:
            em_stats [r ['empresa']]={'cnt':int (r ['cnt']or 0 ),'total':float (r ['total']or 0 )}
    cursor .execute ("SELECT DISTINCT SUBSTRING(fecha::text,1,4) as anio FROM registros WHERE fecha IS NOT NULL ORDER BY anio DESC")
    anios_export =[r [0 ]for r in cursor .fetchall ()]
    cursor .execute ("SELECT DISTINCT fuente FROM registros WHERE fuente IS NOT NULL ORDER BY fuente")
    fuentes_export =[r [0 ]for r in cursor .fetchall ()]
    medidas_empresas =obtener_medidas_empresas (conn )
    agua_map ,_agua_empresas ,_agua_anios ,_agua_data ,total_huella_hidrica_global ,_top_agua =calcular_metricas_agua_admin (cursor ,empresas )
    intensidad_huella_vals =[]
    unidades_huella=set()
    for emp in empresas :
        medida =medidas_empresas .get (emp ['empresa'])if medidas_empresas else None 
        if medida and float (medida .get ('valor')or 0 )>0 :
            huella_anual=agua_map.get(emp['empresa'],{}).get(str(medida['anio']))
            if huella_anual is not None:
                intensidad_huella_vals.append(huella_anual/float(medida['valor']))
                unidades_huella.add(medida.get('unidad'))
    intensidad_huella_promedio=round(sum(intensidad_huella_vals)/len(intensidad_huella_vals),6) if intensidad_huella_vals and len(unidades_huella)==1 else None
    conn .close ()

    return render_template ("admin.html",empresas =empresas ,em_stats =em_stats ,admin_section ="empresas",
    total_empresas =t_emp ,total_registros =t_reg ,total_emisiones =t_em ,total_pendientes =t_pend ,
    total_huella_hidrica_global =total_huella_hidrica_global ,intensidad_huella_promedio =intensidad_huella_promedio ,
    anios_export =anios_export ,fuentes_export =fuentes_export ,
    medidas_empresas =medidas_empresas ,sectores_empresa =SECTORES_EMPRESA ,unidades_productivas =UNIDADES_PRODUCTIVAS ,
    current_year =datetime .now ().year )

@app .route ("/admin/factores",methods =["GET","POST"])
def admin_factores ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    t_emp ,t_reg ,t_em ,t_pend =get_admin_stats ()
    conn =get_db ()
    if request .method =="POST":
        try :
            anio_val =int (request .form .get ("anio")or 0 )
            conn .cursor ().execute ("""
                INSERT INTO factores (categoria, unidad, factor, anio)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (categoria, unidad, anio) DO UPDATE SET factor = EXCLUDED.factor
            """,(request .form .get ("categoria"),request .form .get ("unidad"),float (request .form .get ("factor")),anio_val ))
            conn .commit ()
            flash ("Factor guardado exitosamente","success")
        except Exception as e :
            flash (f"Error al guardar: {e }","error")
            conn .rollback ()

    df =pd .read_sql_query ("SELECT * FROM factores ORDER BY anio DESC, categoria, unidad",conn )
    # pandas convierte NULL a float('nan') — convertir a None para que Jinja2 los trate como falsy
    factores_list =[
    {**{k :(None if (isinstance (v ,float )and pd .isna (v ))else v )for k ,v in row .items ()},
    'clasificacion':_clasificar_factor_catalogo (row .get ('categoria'),row .get ('unidad'),row .get ('nombre_chile'))}
    for row in df .to_dict ('records')
    ]
    cur_elec =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cur_elec .execute ("SELECT anio, mes, sistema, factor_emision_avg FROM factores_electricos ORDER BY sistema, anio, mes")
    factores_elec =[dict (r )for r in cur_elec .fetchall ()]
    resumen_elec ={}
    for f in factores_elec :
        key =(f ['sistema'],f ['anio'])
        resumen_elec [key ]=resumen_elec .get (key ,0 )+1 
    conn .close ()
    return render_template ("admin.html",factores =factores_list ,factores_elec =factores_elec ,resumen_elec =resumen_elec ,admin_section ="factores",
    total_empresas =t_emp ,total_registros =t_reg ,total_emisiones =t_em ,total_pendientes =t_pend ,
    medidas_empresas ={},sectores_empresa =SECTORES_EMPRESA ,unidades_productivas =UNIDADES_PRODUCTIVAS ,
    current_year =datetime .now ().year )

def _huella_permiso_admin ():
    return session .get ("user_id")and session .get ("es_admin")==1 

def _huella_permiso_autenticado ():
    return session .get ("user_id")is not None 

def _huella_es_admin ():
    return session .get ("es_admin")==1 

def _huella_estado_ubicacion (row ):
    completos =[
    row .get ("region"),
    row .get ("comuna"),
    row .get ("latitud"),
    row .get ("longitud"),
    row .get ("codigo_cuenca"),
    row .get ("nombre_cuenca"),
    ]
    llenos =sum (1 for v in completos if v not in (None ,"","nan"))
    if llenos >=len (completos ):
        return "Completa"
    if llenos >=2 :
        return "Parcial"
    return "Pendiente"

def _huella_sede_factor_disponible (cursor ,sede ):
    codigo_cuenca =sede .get ("codigo_cuenca")
    region =sede .get ("region")
    comuna =sede .get ("comuna")
    cursor .execute ("""
        SELECT 1
        FROM factores_escasez_agua
        WHERE activo = TRUE
          AND (
                (nivel_geografico = 'cuenca' AND codigo_geografico = %s)
             OR (nivel_geografico = 'subnacional' AND codigo_geografico IN (%s, %s))
             OR (nivel_geografico = 'pais' AND codigo_geografico = 'Chile')
          )
        LIMIT 1
    """,(codigo_cuenca ,region ,comuna ))
    return cursor .fetchone ()is not None 

@app .route ("/admin/huella-hidrica")
def admin_huella_hidrica():
    if not _huella_permiso_admin():
        return redirect("/")
    t_emp,t_reg,t_em,t_pend=get_admin_stats()
    conn=get_db()
    cursor=conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM agua_sedes ORDER BY empresa,nombre_sede")
    sedes=[dict(r) for r in cursor.fetchall()]
    for sede in sedes:
        sede['estado_ubicacion']=_huella_estado_ubicacion(sede)
        sede['factor_disponible']=_huella_sede_factor_disponible(cursor,sede)
    cursor.execute("SELECT * FROM factores_escasez_agua ORDER BY fecha_carga DESC,nivel_geografico,codigo_geografico")
    factores=[dict(r) for r in cursor.fetchall()]
    cursor.execute("SELECT DISTINCT empresa FROM agua_flujos WHERE empresa IS NOT NULL ORDER BY empresa")
    empresas=[r[0] for r in cursor.fetchall()]
    sede_map={s['id']:s for s in sedes}
    cobertura=[]
    for empresa in empresas:
        data=crear_resumen_agua(conn,empresa)
        for row in resultados_por_periodo(data['flujos'],data['sedes'],data['factores']):
            sede=sede_map.get(row['sede_id'],{})
            cobertura.append({**row,'empresa':empresa,
                'ubicacion_configurada':_huella_estado_ubicacion(sede) if sede else 'Pendiente',
                'factor_disponible':row['factor'] is not None,
                'resultado_calculable':'Sí' if data['estado_medicion_agua']=='ambos' and row['huella_escasez_m3eq'] is not None else 'Pendiente'})
    conn.close()
    return render_template("admin_huella_hidrica.html",total_empresas=t_emp,total_registros=t_reg,
        total_emisiones=t_em,total_pendientes=t_pend,sedes=sedes,factores=factores,
        coberturas=cobertura,admin_section="huella_hidrica")

@app .route ("/admin/huella-hidrica/sedes",methods =["GET","POST"])
def admin_huella_sedes ():
    if not _huella_permiso_admin ():
        return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    try :
        if request .method =="POST":
            sede_id =request .form .get ("sede_id")
            payload =(
            request .form .get ("empresa","").strip (),
            request .form .get ("nombre_sede","").strip (),
            request .form .get ("region","").strip ()or None ,
            request .form .get ("comuna","").strip ()or None ,
            request .form .get ("latitud","").strip ()or None ,
            request .form .get ("longitud","").strip ()or None ,
            request .form .get ("codigo_cuenca","").strip ()or None ,
            request .form .get ("nombre_cuenca","").strip ()or None ,
            request .form .get ("nivel_ubicacion","").strip ()or None ,
            )
            if not payload [0 ]or not payload [1 ]:
                flash ("Empresa y nombre de sede son obligatorios.","error")
            else :
                if sede_id :
                    cursor.execute('SELECT empresa FROM agua_sedes WHERE id=%s',(sede_id,))
                    anterior=cursor.fetchone()
                    if not anterior:
                        raise ValueError('La sede no existe.')
                    if anterior[0]!=payload[0]:
                        cursor.execute('SELECT 1 FROM agua_flujos WHERE sede_id=%s UNION ALL SELECT 1 FROM resultados_huella_agua WHERE sede_id=%s LIMIT 1',(sede_id,sede_id))
                        if cursor.fetchone():
                            raise ValueError('No se puede reasignar una sede que tiene registros de agua.')
                    cursor .execute ("""
                        UPDATE agua_sedes
                        SET empresa=%s, nombre_sede=%s, region=%s, comuna=%s, latitud=%s, longitud=%s, codigo_cuenca=%s, nombre_cuenca=%s, nivel_ubicacion=%s
                        WHERE id=%s
                    """,(*payload ,sede_id ))
                    flash ("Sede actualizada correctamente.","success")
                else :
                    cursor .execute ("""
                        INSERT INTO agua_sedes (empresa, nombre_sede, region, comuna, latitud, longitud, codigo_cuenca, nombre_cuenca, nivel_ubicacion)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,payload )
                    flash ("Sede creada correctamente.","success")
                conn .commit ()
    except Exception as e :
        conn .rollback ()
        flash (f"No se pudo guardar la sede: {e }","danger")
    finally :
        conn .close ()
    return redirect (url_for ("admin_huella_hidrica"))

@app .route ("/admin/huella-hidrica/sedes/<int:sede_id>/toggle",methods =["POST"])
def admin_huella_toggle_sede (sede_id ):
    if not _huella_permiso_admin ():
        return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("UPDATE agua_sedes SET activo = NOT activo WHERE id = %s",(sede_id ,))
    conn .commit ()
    conn .close ()
    flash ("Estado de sede actualizado.","info")
    return redirect (url_for ("admin_huella_hidrica"))

@app .route ("/admin/huella-hidrica/factores",methods =["GET","POST"])
def admin_huella_factores ():
    if not _huella_permiso_admin ():
        return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    try :
        if request .method =="POST":
            factor_id =request .form .get ("factor_id")
            metodo =request .form .get ("metodo","").strip ()
            version_metodo =request .form .get ("version_metodo","").strip ()
            actividad =request .form .get ("actividad","").strip ()
            nivel_geografico =request .form .get ("nivel_geografico","").strip ()
            codigo_geografico =request .form .get ("codigo_geografico","").strip ()
            factor_m3eq_m3 =request .form .get ("factor_m3eq_m3","").strip ()
            fuente =request .form .get ("fuente","").strip ()
            referencia =request .form .get ("referencia","").strip ()
            periodo_inicio =request .form .get ("periodo_inicio")or None 
            periodo_fin =request .form .get ("periodo_fin")or None 
            if not metodo or not version_metodo or not nivel_geografico or not codigo_geografico or not factor_m3eq_m3 or not fuente :
                flash ("Completa método, versión, nivel geográfico, código, factor y fuente.","error")
            else :
                factor_val=numero_finito(factor_m3eq_m3,'El factor de escasez',positivo=True)
                validar_factor_escasez({'metodo':metodo,'version_metodo':version_metodo,
                    'fuente':fuente,'periodo_inicio':periodo_inicio,'periodo_fin':periodo_fin,
                    'factor_m3eq_m3':factor_val})
                if factor_id :
                    cursor .execute ("""
                        UPDATE factores_escasez_agua
                        SET metodo=%s, version_metodo=%s, actividad=%s, nivel_geografico=%s, codigo_geografico=%s,
                            periodo_inicio=%s, periodo_fin=%s, factor_m3eq_m3=%s, fuente=%s, referencia=%s
                        WHERE id=%s
                    """,(metodo ,version_metodo ,actividad ,nivel_geografico ,codigo_geografico ,periodo_inicio ,periodo_fin ,factor_val ,fuente ,referencia ,factor_id ))
                    flash ("Factor actualizado correctamente.","success")
                else :
                    cursor .execute ("""
                        INSERT INTO factores_escasez_agua (metodo, version_metodo, actividad, nivel_geografico, codigo_geografico, periodo_inicio, periodo_fin, factor_m3eq_m3, fuente, referencia)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,(metodo ,version_metodo ,actividad ,nivel_geografico ,codigo_geografico ,periodo_inicio ,periodo_fin ,factor_val ,fuente ,referencia ))
                    flash ("Factor creado correctamente.","success")
                conn .commit ()
    except Exception as e :
        conn .rollback ()
        flash (f"No se pudo guardar el factor: {e }","danger")
    finally :
        conn .close ()
    return redirect (url_for ("admin_huella_hidrica"))

@app .route ("/admin/huella-hidrica/factores/<int:factor_id>/toggle",methods =["POST"])
def admin_huella_toggle_factor (factor_id ):
    if not _huella_permiso_admin ():
        return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("UPDATE factores_escasez_agua SET activo = NOT activo WHERE id = %s",(factor_id ,))
    conn .commit ()
    conn .close ()
    flash ("Estado del factor actualizado.","info")
    return redirect (url_for ("admin_huella_hidrica"))

@app .route ("/admin/huella-hidrica/factores/preview",methods =["POST"])
def admin_huella_preview_factores ():
    if not _huella_permiso_admin ():
        return redirect ("/")
    archivo =request .files .get ("archivo_factores")
    if not archivo or archivo .filename =="":
        flash ("Selecciona un archivo Excel o CSV.","danger")
        return redirect (url_for ("admin_huella_hidrica"))
    try :
        if archivo .filename .lower ().endswith (".csv"):
            df =pd .read_csv (archivo )
        else :
            df =pd .read_excel (archivo )
        filas =[]
        errores =[]
        columnas_requeridas =["metodo","version_metodo","actividad","nivel_geografico","codigo_geografico","factor_m3eq_m3","fuente","referencia","periodo_inicio","periodo_fin"]
        for col in columnas_requeridas :
            if col not in df .columns :
                errores .append (f"Falta la columna requerida: {col }")
        for idx ,row in df .iterrows ():
            try :
                factor_val =float (str (row .get ("factor_m3eq_m3","")).replace (",","."))
                if factor_val <=0 :
                    raise ValueError ("factor <= 0")
                if not str (row .get ("metodo","")).strip ()or not str (row .get ("version_metodo","")).strip ()or not str (row .get ("fuente","")).strip ():
                    raise ValueError ("metadatos incompletos")
                filas .append (row .to_dict ())
            except Exception as e :
                errores .append (f"Fila {idx +2 }: {e }")
        return render_template ("admin_huella_factores_preview.html",filas =filas ,errores =errores ,datos_json =json .dumps (filas ,default =str ))
    except Exception as e :
        flash (f"No se pudo leer el archivo: {e }","danger")
        return redirect (url_for ("admin_huella_hidrica"))

@app .route ("/admin/huella-hidrica/factores/cargar",methods =["POST"])
def admin_huella_cargar_factores ():
    if not _huella_permiso_admin ():
        return redirect ("/")
    datos_json =request .form .get ("datos_json")
    if not datos_json :
        flash ("No hay datos para cargar.","danger")
        return redirect (url_for ("admin_huella_hidrica"))
    filas =json .loads (datos_json )
    conn =get_db ()
    cursor =conn .cursor ()
    try :
        insertados =0 
        for row in filas :
            factor_val =float (str (row .get ("factor_m3eq_m3","")).replace (",","."))
            if factor_val <=0 :
                continue 
            cursor .execute ("""
                INSERT INTO factores_escasez_agua
                (metodo, version_metodo, actividad, nivel_geografico, codigo_geografico, periodo_inicio, periodo_fin, factor_m3eq_m3, fuente, referencia)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,(
            row .get ("metodo"),row .get ("version_metodo"),row .get ("actividad"),row .get ("nivel_geografico"),
            row .get ("codigo_geografico"),row .get ("periodo_inicio")or None ,row .get ("periodo_fin")or None ,
            factor_val ,row .get ("fuente"),row .get ("referencia"),
            ))
            insertados +=1 
        conn .commit ()
        flash (f"Se cargaron {insertados } factores correctamente.","success")
    except Exception as e :
        conn .rollback ()
        flash (f"No se pudo cargar la información: {e }","danger")
    finally :
        conn .close ()
    return redirect (url_for ("admin_huella_hidrica"))

@app .route ("/huella-hidrica/sedes")
def huella_sedes_publico ():
    if not _huella_permiso_autenticado ():
        return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    if _huella_es_admin ():
        cursor .execute ("SELECT * FROM agua_sedes ORDER BY empresa, nombre_sede")
    else :
        cursor .execute ("SELECT * FROM agua_sedes WHERE empresa = %s ORDER BY nombre_sede",(session .get ("empresa"),))
    sedes =[dict (r )for r in cursor .fetchall ()]
    conn .close ()
    return render_template ("huella_hidrica_sedes.html",sedes =sedes ,es_admin =_huella_es_admin ())

@app .route ("/admin/editar_empresa/<int:empresa_id>",methods =["POST"])
def admin_editar_empresa (empresa_id ):
    if not can_manage_companies ():return redirect ("/")
    nombre =request .form .get ("empresa","").strip ()
    email =request .form .get ("email","").strip ()
    contacto =request .form .get ("contacto","").strip ()
    rut =request .form .get ("rut","").strip ()
    sector_empresa =request .form .get ("sector_empresa","").strip ()or None 
    tipo_empresa =request .form .get ("tipo_empresa","").strip ()or None 
    medida_anio =request .form .get ("medida_anio","").strip ()
    medida_unidad =request .form .get ("medida_unidad","").strip ()
    medida_valor =request .form .get ("medida_valor","").strip ()
    if not nombre or not email :
        flash ("Nombre y email son obligatorios.","error")
        return redirect (url_for ('admin_empresas'))
    conn =get_db ()
    cursor =conn .cursor ()
    try :
        cursor .execute ("SELECT empresa FROM usuarios WHERE id = %s AND es_admin = 0 AND COALESCE(NULLIF(rol,''),'empresa') = 'empresa'",(empresa_id ,))
        row =cursor .fetchone ()
        if not row :
            flash ("Empresa no encontrada.","error")
            conn .close ()
            return redirect (url_for ('admin_empresas'))
        nombre_anterior =row [0 ]
        cursor .execute ("""
            UPDATE usuarios SET empresa = %s, email = %s, contacto = %s, rut = %s, sector_empresa = %s, tipo_empresa = %s
            WHERE id = %s AND es_admin = 0
        """,(nombre ,email ,contacto ,rut ,sector_empresa ,tipo_empresa ,empresa_id ))
        if nombre !=nombre_anterior :
            for tabla in ['registros','resultados_huella_suministro_agua','resultados_huella_agua','combustible_movil','vehiculos','pdf_uploads','pending_pdf_uploads','tickets',
            'agua_sedes','agua_flujos','agua_consumo','agua_cuencas','agua_afluentes','agua_costos','residuos_registros',
            'irec_certificados','configuracion','energeticos_empresa','medidas_productivas']:
                cursor .execute (f"UPDATE {tabla } SET empresa = %s WHERE empresa = %s",(nombre ,nombre_anterior ))
        if medida_anio and medida_unidad and medida_valor :
            anio_int =int (medida_anio )
            valor_float =float (medida_valor .replace (',','.'))
            guardar_medida_productiva (cursor ,nombre ,anio_int ,medida_unidad ,valor_float )
        conn .commit ()
        flash (f"Empresa '{nombre }' actualizada correctamente.","success")
    except psycopg2 .IntegrityError :
        conn .rollback ()
        flash ("Error: ese email ya está en uso por otra cuenta.","error")
    except Exception as e :
        conn .rollback ()
        flash (f"Error al editar: {e }","danger")
    finally :
        conn .close ()
    return redirect (url_for ('admin_empresas'))


@app .route ("/admin/resetear_password/<int:empresa_id>",methods =["POST"])
def admin_resetear_password (empresa_id ):
    if not can_manage_companies ():return redirect ("/")
    nueva =request .form .get ("nueva_password","").strip ()
    if len (nueva )<6 :
        flash ("La contraseña debe tener al menos 6 caracteres.","error")
        return redirect (url_for ('admin_empresas'))
    conn =get_db ()
    cursor =conn .cursor ()
    try :
        cursor .execute ("SELECT empresa FROM usuarios WHERE id = %s AND es_admin = 0 AND COALESCE(NULLIF(rol,''),'empresa') = 'empresa'",(empresa_id ,))
        row =cursor .fetchone ()
        if not row :
            flash ("Empresa no encontrada.","error")
            conn .close ()
            return redirect (url_for ('admin_empresas'))
        cursor .execute ("UPDATE usuarios SET password = %s WHERE id = %s",(hash_password (nueva ),empresa_id ))
        conn .commit ()
        flash (f"Contraseña de '{row [0 ]}' reseteada correctamente.","success")
    except Exception as e :
        conn .rollback ()
        flash (f"Error: {e }","danger")
    finally :
        conn .close ()
    return redirect (url_for ('admin_empresas'))


@app .route ("/admin/eliminar_empresa/<int:empresa_id>",methods =["POST"])
def admin_eliminar_empresa (empresa_id ):
    if session .get ('es_admin')!=1 :return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    try :
        cursor .execute ("SELECT empresa FROM usuarios WHERE id = %s AND es_admin = 0 AND COALESCE(NULLIF(rol,''),'empresa') = 'empresa'",(empresa_id ,))
        row =cursor .fetchone ()
        if not row :
            flash ("Empresa no encontrada.","error")
            conn .close ()
            return redirect (url_for ('admin_empresas'))
        nombre =row [0 ]
        for tabla in ['resultados_huella_suministro_agua','resultados_huella_agua','registros','combustible_movil','vehiculos','pdf_uploads','pending_pdf_uploads','tickets',
        'agua_flujos','agua_sedes','agua_consumo','agua_cuencas','agua_afluentes','agua_costos','residuos_registros',
        'irec_certificados','configuracion','energeticos_empresa','medidas_productivas']:
            cursor .execute (f"DELETE FROM {tabla } WHERE empresa = %s",(nombre ,))
        cursor .execute ("DELETE FROM usuarios WHERE id = %s AND es_admin = 0",(empresa_id ,))
        conn .commit ()
        flash (f"Empresa '{nombre }' y todos sus datos han sido eliminados.","success")
    except Exception as e :
        conn .rollback ()
        flash (f"Error al eliminar: {e }","danger")
    finally :
        conn .close ()
    return redirect (url_for ('admin_empresas'))


@app .route ("/admin/crear_empresa",methods =["POST"])
def admin_crear_empresa ():
    if not can_manage_companies ():return redirect ("/")
    empresa=(request.form.get('empresa') or '').strip()
    email=(request.form.get('email') or '').strip()
    nueva=request.form.get('password') or ''
    if not empresa or not email or len(nueva)<6 or len(nueva.encode('utf-8'))>72:
        flash('Nombre, correo y contraseña de al menos 6 caracteres (máximo 72 bytes) son obligatorios.','error')
        return redirect(url_for('admin_empresas'))
    password =hash_password(nueva)
    contacto ,rut =request .form .get ("contacto"),request .form .get ("rut")
    sector_empresa =request .form .get ("sector_empresa","").strip ()or None 
    tipo_empresa =request .form .get ("tipo_empresa","").strip ()or None 

    conn =get_db ()
    cursor =conn .cursor ()
    try :
        cursor .execute ("""
            INSERT INTO usuarios (empresa, email, password, contacto, rut, sector_empresa, tipo_empresa, fecha_registro, es_admin)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0)
        """,(empresa ,email ,password ,contacto ,rut ,sector_empresa ,tipo_empresa ,datetime .now ().strftime ("%Y-%m-%d %H:%M")))
        conn .commit ()
        flash (f"Empresa '{empresa }' creada exitosamente.","success")
    except psycopg2 .IntegrityError :
        conn .rollback ()
        flash ("Error: El correo electrónico ya está en uso.","error")
    finally :conn .close ()
    return redirect (url_for ('admin_empresas'))

@app .route ("/admin/debug_excel",methods =["POST"])
def admin_debug_excel ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    file =request .files .get ('archivo_factores')
    if not file or file .filename =='':
        return "No se subió archivo",400 
    df =pd .read_excel (file ,sheet_name ='Equivalencias',header =None ,engine ='openpyxl')
    html =['<style>table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px;font-size:12px;white-space:nowrap}</style>']
    html .append (f'<p><b>Filas totales:</b> {len (df )} &nbsp; <b>Columnas:</b> {len (df .columns )}</p>')
    html .append ('<table><thead><tr><th>Fila</th>')
    for c in df .columns :
        html .append (f'<th>Col {c }</th>')
    html .append ('</tr></thead><tbody>')
    for i in range (min (12 ,len (df ))):
        html .append (f'<tr><td><b>{i }</b></td>')
        for val in df .iloc [i ]:
            cell =str (val )if pd .notna (val )else '<span style="color:#ccc">—</span>'
            html .append (f'<td>{cell }</td>')
        html .append ('</tr>')
    html .append ('</tbody></table>')
    return ''.join (html )

@app .route ("/admin/eliminar_factor/<categoria>/<unidad>/<int:anio>",defaults ={'tratamiento':''})
@app .route ("/admin/eliminar_factor/<categoria>/<unidad>/<int:anio>/<tratamiento>")
def admin_eliminar_factor (categoria ,unidad ,anio ,tratamiento ):
    if session .get ('es_admin')!=1 :return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    if tratamiento :
        cursor .execute (
        "DELETE FROM factores WHERE categoria = %s AND unidad = %s AND anio = %s AND tratamiento = %s",
        (categoria ,unidad ,anio ,tratamiento ))
    else :
        cursor .execute (
        "DELETE FROM factores WHERE categoria = %s AND unidad = %s AND anio = %s AND (tratamiento IS NULL OR tratamiento = '')",
        (categoria ,unidad ,anio ))
    conn .commit ()
    conn .close ()
    flash ("Factor eliminado.","info")
    return redirect (url_for ('admin_factores'))

@app .route ("/admin/cargar_factores",methods =["POST"])
def cargar_factores ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    if 'archivo_factores'not in request .files or request .files ['archivo_factores'].filename =='':
        flash ("No se subió ningún archivo","danger")
        return redirect (request .referrer )

    file =request .files ['archivo_factores']
    try :
        anio_factores =int (request .form .get ("anio_factores")or 0 )
        df =pd .read_excel (file ,sheet_name ='Equivalencias',header =None ,engine ='openpyxl')
        conn =get_db ()
        cursor =conn .cursor ()
        if anio_factores :
            cursor .execute ("DELETE FROM factores WHERE anio = %s",(anio_factores ,))
        else :
            cursor .execute ("DELETE FROM factores WHERE anio = 0")
        actualizados =0 

        def safe_get (row_data ,idx ):
            if idx >=len (row_data ):
                return None 
            val =row_data .iloc [idx ]
            s =str (val ).strip ()
            return s if pd .notna (val )and s .lower ()not in ('nan','none','')else None 

        def safe_float (val ):
            if val is None :return None 
            try :return float (str (val ).replace (',','.'))
            except :return None 

            # Índices fijos confirmados para la hoja Equivalencias
            # Col 2=cat combustible, 5=unidad, 7=factor comb
            # Col 12=cat refrigerante, 14=factor ref
            # Col 19=categoría DEFRA residuo, 21=tratamiento, 22=equivalencia Chile, 23=factor residuo
        IDX_DEFRA =19 
        IDX_TRAT =21 
        IDX_EQUIV =22 # "Equivalencia en Chile"
        IDX_FE_RES =23 

        residuos_map ={}# {defra_name: {'fe', 'nombre_chile', 'trat'}}

        for index ,row in df .iterrows ():
            if index <3 :
                continue 

                # Combustibles
            cat_comb =safe_get (row ,2 )
            uni_comb =safe_get (row ,5 )or 'N/A'
            fe_comb =safe_float (safe_get (row ,7 ))
            if cat_comb and fe_comb is not None :
                cursor .execute ("""
                    INSERT INTO factores (categoria, unidad, factor, anio) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (categoria, unidad, anio, COALESCE(tratamiento, ''))
                    DO UPDATE SET factor = EXCLUDED.factor
                """,(cat_comb ,uni_comb ,fe_comb ,anio_factores ))
                actualizados +=1 

                # Refrigerantes
            cat_ref =safe_get (row ,12 )
            fe_ref =safe_float (safe_get (row ,14 ))
            if cat_ref and fe_ref is not None :
                cursor .execute ("""
                    INSERT INTO factores (categoria, unidad, factor, anio) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (categoria, unidad, anio, COALESCE(tratamiento, ''))
                    DO UPDATE SET factor = EXCLUDED.factor
                """,(cat_ref ,'kg',fe_ref ,anio_factores ))
                actualizados +=1 

                # Residuos: guardar un registro por (DEFRA, tratamiento) — no colapsar al máximo
            cat_defra =safe_get (row ,IDX_DEFRA )
            equiv_chile =safe_get (row ,IDX_EQUIV )
            trat_esp =safe_get (row ,IDX_TRAT )or ''
            fe_res =safe_float (safe_get (row ,IDX_FE_RES ))

            if cat_defra and fe_res is not None and fe_res >0 :
                key =(cat_defra ,trat_esp )
                existing =residuos_map .get (key )
                if existing is None :
                    residuos_map [key ]={'fe':fe_res ,'nombre_chile':equiv_chile ,'trat':trat_esp }
                else :
                    if equiv_chile and not existing ['nombre_chile']:
                        existing ['nombre_chile']=equiv_chile 

        for (defra ,trat_key ),data in residuos_map .items ():
            cursor .execute ("""
                INSERT INTO factores (categoria, unidad, factor, nombre_chile, tratamiento, anio)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (categoria, unidad, anio, COALESCE(tratamiento, ''))
                DO UPDATE
                    SET factor = EXCLUDED.factor,
                        nombre_chile = EXCLUDED.nombre_chile
            """,(defra ,'tonne',data ['fe'],data ['nombre_chile'],trat_key or None ,anio_factores ))
            actualizados +=1 

        conn .commit ()
        conn .close ()
        flash (f"¡Sincronización exitosa! Se actualizaron {actualizados } factores.","success")
    except Exception as e :
        flash (f"Error técnico al leer el Excel. Detalle: {str (e )}","danger")
    return redirect (request .referrer )

@app .route ("/admin/preview_electricidad",methods =["POST"])
def preview_electricidad ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    archivo =request .files .get ("archivo_electricidad")
    if not archivo or archivo .filename =="":
        flash ("No se seleccionó ningún archivo.","danger")
        return redirect (url_for ('admin_factores'))
    try :
        filas = _leer_factores_electricos(archivo)
        errores =[]
        sistemas =sorted (set (f ['sistema']for f in filas ))
        anios =sorted (set (f ['anio']for f in filas ))
        return render_template ("admin_preview_electricidad.html",
        filas =filas ,errores =errores ,
        sistemas =sistemas ,anios =anios ,
        datos_json =json .dumps (filas ))
    except Exception as e :
        flash (f"Error al leer el Excel/CSV: {str (e )}","danger")
        return redirect (url_for ('admin_factores'))

@app .route ("/admin/cargar_electricidad",methods =["POST"])
def cargar_electricidad ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    datos_json =request .form .get ('datos_json')
    if not datos_json :
    # Carga directa legacy (sin preview)
        archivo =request .files .get ("archivo_electricidad")
        if not archivo or archivo .filename =="":
            flash ("No se seleccionó ningún archivo.","danger")
            return redirect (request .referrer )
        try :
            filas = _leer_factores_electricos(archivo)
            datos_json =json .dumps (filas )
        except Exception as e :
            flash (f"Error al leer el Excel/CSV: {str (e )}","danger")
            return redirect (request .referrer )
    try :
        filas =json .loads (datos_json )
        conn =get_db ()
        cursor =conn .cursor ()
        for f in filas :
            cursor .execute (
            "DELETE FROM factores_electricos WHERE anio = %s AND mes = %s AND sistema = %s",
            (f ['anio'],f ['mes'],f ['sistema']))
            cursor .execute (
            "INSERT INTO factores_electricos (anio, mes, sistema, factor_emision_avg) VALUES (%s, %s, %s, %s)",
            (f ['anio'],f ['mes'],f ['sistema'],f ['factor'])
            )
        conn .commit ()
        conn .close ()
        flash (f"¡{len (filas )} factores eléctricos cargados correctamente!","success")
    except Exception as e :
        flash (f"Error al guardar: {str (e )}","danger")
    return redirect (url_for ('admin_factores'))

@app .route ("/admin/empresa/<string:nombre_empresa>")
def admin_detalle_empresa (nombre_empresa ):
    if not can_manage_companies ():return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )

    cursor .execute ("SELECT empresa, email, contacto, rut, sector_empresa, tipo_empresa, fecha_registro FROM usuarios WHERE empresa = %s AND es_admin = 0",(nombre_empresa ,))
    empresa_info =cursor .fetchone ()
    cursor .execute ("SELECT COUNT(*) as total_reg, SUM(emision) as total_emi FROM registros WHERE empresa = %s",(nombre_empresa ,))
    kpis =cursor .fetchone ()

    cursor .execute ("SELECT fuente, SUM(emision) as total FROM registros WHERE empresa = %s GROUP BY fuente",(nombre_empresa ,))
    datos_fuente =[dict (row )for row in cursor .fetchall ()]

    cursor .execute ("SELECT SUBSTRING(fecha, 1, 7) as mes, SUM(emision) as total FROM registros WHERE empresa = %s GROUP BY mes ORDER BY mes",(nombre_empresa ,))
    datos_mes =[dict (row )for row in cursor .fetchall ()]

    cursor .execute ("SELECT DISTINCT SUBSTRING(fecha::text,1,4) as anio FROM registros WHERE empresa = %s AND fecha IS NOT NULL ORDER BY anio DESC",(nombre_empresa ,))
    anios_disponibles =[r [0 ]for r in cursor .fetchall ()]

    cursor .execute ("SELECT DISTINCT fuente FROM registros WHERE empresa = %s AND fuente IS NOT NULL ORDER BY fuente",(nombre_empresa ,))
    fuentes_disponibles =[r [0 ]for r in cursor .fetchall ()]
    medidas_productivas =obtener_medidas_productivas (conn ,nombre_empresa )
    medida_actual =medidas_productivas [0 ]if medidas_productivas else None 
    conn .close ()

    return render_template ("admin_detalle.html",empresa =nombre_empresa ,kpis =kpis ,
    datos_fuente =datos_fuente ,datos_mes =datos_mes ,
    anios_disponibles =anios_disponibles ,fuentes_disponibles =fuentes_disponibles ,
    empresa_info =empresa_info ,medidas_productivas =medidas_productivas ,
    medida_actual =medida_actual )

@app .route ("/admin/exportar/<string:nombre_empresa>")
def exportar_datos_empresa (nombre_empresa ):
    if not can_manage_companies ():return redirect ("/")
    anio =request .args .get ('anio','Todos')
    fuente =request .args .get ('fuente','').strip ()
    alcance =request .args .get ('alcance','').strip ()

    where =["empresa = %s"]
    params =[nombre_empresa ]
    if anio !='Todos':
        where .append ("SUBSTRING(fecha::text,1,4) = %s");params .append (anio )
    if fuente :
        where .append ("fuente = %s");params .append (fuente )
    if alcance :
        _case =("CASE WHEN fuente IN ('Combustión Fija','Combustible Móvil','Combustión Estacionaria','Refrigerantes','Fugas de Refrigerantes') THEN 'Alcance 1'"
        " WHEN fuente='Electricidad' THEN 'Alcance 2' WHEN fuente='Residuos' THEN 'Alcance 3' ELSE COALESCE(alcance,'') END")
        where .append (f"({_case }) = %s");params .append (alcance )

    where_sql =" AND ".join (where )
    conn =get_db ()
    df =pd .read_sql_query (
    f"SELECT fecha, area, alcance, fuente, categoria, actividad, cantidad, unidad, factor, emision "
    f"FROM registros WHERE {where_sql } ORDER BY fecha DESC",
    conn ,params =params )
    conn .close ()

    if df .empty :
        flash (f"La empresa no tiene registros para el periodo seleccionado ({anio }).","warning")
        return redirect (url_for('admin_detalle_empresa',nombre_empresa=nombre_empresa))

    _etiquetar_unidad_factor(df)
    df.rename(columns={'factor': 'Factor de emisión'}, inplace=True)
    output =io .BytesIO ()
    with pd .ExcelWriter (output ,engine ='openpyxl')as writer :
        nombre_hoja =f'Auditoria_{anio }'
        df .to_excel (writer ,sheet_name =nombre_hoja ,index =False )
        worksheet =writer .sheets [nombre_hoja ]
        for columna in ['A','B','C','D','E','F','G','H','I','J']:
            worksheet .column_dimensions [columna ].width =18 

    output .seek (0 )
    nombre_archivo =f"Reporte_Auditoria_{nombre_empresa .replace (' ','_')}_{anio }.xlsx"
    return send_file (output ,download_name =nombre_archivo ,as_attachment =True )


@app .route ("/admin/exportar_todo")
def admin_exportar_todo ():
    if session .get ('es_admin')!=1 :
        return redirect ("/")

    anio =request .args .get ('anio','Todos')
    empresa_filtro =request .args .get ('empresa','').strip ()
    fuente_filtro =request .args .get ('fuente','').strip ()
    alcance_filtro =request .args .get ('alcance','').strip ()

    where =["1=1"]
    params =[]
    if anio !='Todos':
        where .append ("SUBSTRING(fecha::text,1,4) = %s");params .append (anio )
    if empresa_filtro :
        where .append ("empresa = %s");params .append (empresa_filtro )
    if fuente_filtro :
        where .append ("fuente = %s");params .append (fuente_filtro )
    if alcance_filtro :
        _case =("CASE WHEN fuente IN ('Combustión Fija','Combustible Móvil','Combustión Estacionaria','Refrigerantes','Fugas de Refrigerantes') THEN 'Alcance 1'"
        " WHEN fuente='Electricidad' THEN 'Alcance 2' WHEN fuente='Residuos' THEN 'Alcance 3' ELSE COALESCE(alcance,'') END")
        where .append (f"({_case }) = %s");params .append (alcance_filtro )
    where_sql =" AND ".join (where )

    conn =get_db ()
    df_total =pd .read_sql_query (
    f"SELECT empresa, fecha, area, alcance, fuente, categoria, actividad, cantidad, unidad, factor, emision "
    f"FROM registros WHERE {where_sql } ORDER BY empresa, fecha DESC",
    conn ,params =params if params else None 
    )
    empresas =pd .read_sql_query (
    "SELECT empresa FROM usuarios WHERE es_admin = 0 AND COALESCE(NULLIF(rol,''),'empresa') = 'empresa' ORDER BY empresa",
    conn 
    )['empresa'].tolist ()
    conn .close ()

    if df_total .empty :
        flash ("No hay registros en la plataforma para exportar.","warning")
        return redirect (url_for ('admin_dashboard'))

    _etiquetar_unidad_factor(df_total)
    df_total .rename (columns ={
    'empresa':'Empresa','fecha':'Fecha','area':'Área',
    'alcance':'Alcance','fuente':'Fuente','categoria':'Combustible/Categoría',
    'actividad':'Uso','cantidad':'Cantidad','unidad':'Unidad',
    'factor':'Factor de emisión','emision':'Emisiones (kg CO2)'
    },inplace =True )

    output =io .BytesIO ()
    with pd .ExcelWriter (output ,engine ='openpyxl')as writer :
        df_total .to_excel (writer ,sheet_name ='Todos',index =False )
        ws =writer .sheets ['Todos']
        for col in ws .columns :
            ws .column_dimensions [col [0 ].column_letter ].width =18 

        for emp in empresas :
            df_emp =df_total [df_total ['Empresa']==emp ]
            if df_emp .empty :
                continue 
            nombre_hoja =emp [:31 ].replace ('/','-').replace ('\\','-').replace ('*','').replace ('?','').replace ('[','').replace (']','').replace (':','')
            df_emp .to_excel (writer ,sheet_name =nombre_hoja ,index =False )
            ws_emp =writer .sheets [nombre_hoja ]
            for col in ws_emp .columns :
                ws_emp .column_dimensions [col [0 ].column_letter ].width =18 

    output .seek (0 )
    nombre_archivo =f"GreenTrack_Exportacion_Total_{datetime .now ().strftime ('%Y%m%d_%H%M')}.xlsx"
    return send_file (output ,download_name =nombre_archivo ,as_attachment =True )


    # ================= OTROS MÓDULOS (Vehículos) =================
@app .route ("/vehiculos")
def vehiculos ():
    if 'user_id'not in session :return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("SELECT * FROM vehiculos WHERE empresa = %s ORDER BY patente",(session .get ('empresa'),))
    vehiculos_data =cursor .fetchall ()
    conn .close ()
    return render_template ("vehiculos.html",vehiculos_data =vehiculos_data )

def _validar_datos_vehiculo(data):
    if not isinstance(data, dict):
        raise ValueError("El cuerpo de la solicitud debe ser JSON.")

    patente =str(data.get('patente') or '').strip().upper()
    if not patente:
        raise ValueError("Patente obligatoria")
    if len(patente) > 20 or any(ord(char) < 32 for char in patente):
        raise ValueError("La patente no puede superar 20 caracteres.")

    anio_raw =data.get('anio')
    anio =None
    if anio_raw not in (None, ''):
        anio_text =str(anio_raw).strip()
        if not anio_text.isdigit():
            raise ValueError("El año debe ser un número entero.")
        anio =int(anio_text)
        if not 1900 <= anio <= datetime.now().year:
            raise ValueError(f"El año debe estar entre 1900 y {datetime.now().year}.")
    return patente, anio

@app .route ("/api/vehiculos",methods =["GET","POST"])
def api_vehiculos ():
    if 'user_id'not in session :return jsonify ({"success":False ,"message":"No autorizado"}),401 
    empresa =session .get ('empresa')
    if request .method =="GET":
        conn =get_db ()
        cursor =conn .cursor ()
        cursor .execute ("SELECT id, patente, tipo, marca, modelo, anio FROM vehiculos WHERE empresa = %s ORDER BY patente",(empresa ,))
        vehiculos =cursor .fetchall ()
        conn .close ()
        return jsonify ([{"id":v [0 ],"patente":v [1 ],"tipo":v [2 ],"marca":v [3 ],"modelo":v [4 ],"anio":v [5 ]}for v in vehiculos ])
    elif request .method =="POST":
        data =request .get_json (silent=True)
        try:
            patente, anio = _validar_datos_vehiculo(data)
        except ValueError as exc:
            return jsonify ({"success":False ,"message":str(exc)}),400

        conn =get_db ()
        cursor =conn .cursor ()
        cursor .execute ("SELECT id FROM vehiculos WHERE empresa = %s AND patente = %s",(empresa ,patente))
        if cursor .fetchone ():
            conn .close ()
            return jsonify ({"success":False ,"message":"Patente ya registrada"}),400 
        cursor .execute ("""
            INSERT INTO vehiculos (empresa, patente, tipo, marca, modelo, anio, fecha_registro)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id;
        """,(empresa ,patente,data .get ('tipo'),data .get ('marca'),data .get ('modelo'),anio,datetime .now ().strftime ("%Y-%m-%d %H:%M")))
        vehiculo_id =cursor .fetchone ()[0 ]
        conn .commit ()
        conn .close ()
        return jsonify ({"success":True ,"message":"Vehículo guardado","vehiculo":{"id":vehiculo_id ,"patente":patente}})

@app .route ("/api/vehiculos/<int:id>", methods=["PUT"])
def api_editar_vehiculo (id):
    if 'user_id'not in session:return jsonify ({"success":False ,"message":"No autorizado"}),401
    try:
        patente, anio = _validar_datos_vehiculo(request.get_json(silent=True))
    except ValueError as exc:
        return jsonify ({"success":False ,"message":str(exc)}),400

    empresa =session.get('empresa')
    conn =get_db()
    cursor =conn.cursor()
    try:
        cursor.execute("SELECT id FROM vehiculos WHERE empresa = %s AND patente = %s AND id <> %s", (empresa, patente, id))
        if cursor.fetchone():
            return jsonify ({"success":False ,"message":"Patente ya registrada"}),400
        cursor.execute("""
            UPDATE vehiculos
            SET patente = %s, tipo = %s, marca = %s, modelo = %s, anio = %s
            WHERE id = %s AND empresa = %s
        """, (patente, request.json.get('tipo'), request.json.get('marca'), request.json.get('modelo'), anio, id, empresa))
        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify ({"success":False ,"message":"No se encontró el vehículo."}),404
        conn.commit()
        return jsonify ({"success":True ,"message":"Vehículo actualizado","vehiculo":{"id":id,"patente":patente}})
    except Exception:
        conn.rollback()
        app.logger.exception('Error al actualizar el vehículo %s', id)
        return jsonify ({"success":False ,"message":"No se pudo actualizar el vehículo."}),500
    finally:
        conn.close()

@app .route ("/vehiculos/eliminar/<int:id>", methods=["POST"])
def eliminar_vehiculo (id ):
    if 'user_id'not in session :return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    try:
        cursor.execute("UPDATE combustible_movil SET vehiculo_id = NULL WHERE vehiculo_id = %s AND empresa = %s", (id, session.get('empresa')))
        cursor.execute("DELETE FROM vehiculos WHERE id = %s AND empresa = %s", (id, session.get('empresa')))
        eliminado = cursor.rowcount > 0
        conn.commit()
        flash("Vehículo eliminado; los consumos históricos se conservan." if eliminado else "No se encontró el vehículo.", "info")
    except Exception:
        conn.rollback()
        flash("No se pudo eliminar el vehículo. Inténtalo nuevamente.", "error")
    finally:
        conn.close()
    return redirect (url_for ('vehiculos'))

@app .route ("/combustible/movil/eliminar/<int:id>")
def eliminar_registro_combustible (id ):
    if 'user_id'not in session :return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    try:
        eliminado = eliminar_consumo_movil(cursor, session.get('empresa'), id)
        conn.commit()
        flash("Registro de combustible eliminado" if eliminado else "No se encontró el registro de combustible.", "info")
    except Exception:
        conn.rollback()
        flash("No se pudo eliminar el consumo. No se guardaron cambios.", "error")
    finally:
        conn.close()
    return redirect (url_for ('combustible_movil'))

@app .route ("/combustible/movil")
def combustible_movil ():
    if 'user_id'not in session :return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("SELECT * FROM vehiculos WHERE empresa = %s",(session .get ('empresa'),))
    vehiculos =cursor .fetchall ()
    conn .close ()
    return render_template ("combustible_movil.html",vehiculos =vehiculos )

@app .route ("/api/combustible/movil",methods =["POST"])
def api_combustible_movil ():
    if 'user_id'not in session :return jsonify ({"success":False ,"message":"No autorizado"}),401 
    empresa =session .get ('empresa')
    data =request.get_json(silent=True)
    if not isinstance(data, dict) or not data.get('registros'):
        return jsonify ({"success":False ,"message":"No se recibieron registros"}),400 

    if not isinstance(data['registros'], list):
        return jsonify({'success':False,'message':'Los registros deben ser una lista.'}),400

    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    guardados =0 
    errores =[]

    for i ,registro in enumerate (data .get ('registros',[])):
        cursor.execute('SAVEPOINT consumo_movil')
        try :
            vehiculo_id =int (registro ['vehiculo_id'])
            periodo =registro ['periodo']# YYYY-MM
            fecha =fecha_periodo(periodo).isoformat()
            cantidad =numero_finito(registro['cantidad'], 'La cantidad', positivo=True)

            combustible =registro ['combustible']
            unidad =registro ['unidad']
            costo =numero_finito(registro.get('costo') or 0, 'El costo')
            if combustible == 'electricidad':
                raise ValueError('Registra la electricidad en Alcance 2 para aplicar el factor del período y su origen.')
            categoria, factor =resolver_factor_carbono(cursor, combustible, unidad, int(periodo[:4]))
            emision =round (cantidad *factor ,4 )

            # Obtener patente del vehículo para registros
            cursor .execute ("SELECT patente, tipo FROM vehiculos WHERE id = %s AND empresa = %s",(vehiculo_id ,empresa ))
            vehiculo =cursor .fetchone ()
            if not vehiculo:
                raise ValueError('El vehículo no existe o no pertenece a la empresa.')
            patente =vehiculo['patente']
            tipo_v =vehiculo['tipo']
            actividad =f"{patente } ({tipo_v })"if tipo_v else patente 

            # 1. Insertar en registros (tabla maestra de emisiones)
            cursor .execute ("""
                INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """,(fecha ,empresa ,'Flota Vehicular','Alcance 1',
            'Combustible Móvil',categoria ,actividad ,
            unidad ,cantidad ,factor ,emision ))
            registro_id = cursor.fetchone()[0]
            calcular_huella_suministro_registro(cursor, registro_id)

            # 2. Insertar en combustible_movil (para tracking por vehículo)
            cursor .execute ("""
                INSERT INTO combustible_movil (empresa, vehiculo_id, periodo, combustible, cantidad, unidad, costo, fecha_registro, registro_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,(empresa ,vehiculo_id ,periodo ,combustible ,cantidad ,unidad ,costo ,fecha, registro_id ))

            cursor.execute('RELEASE SAVEPOINT consumo_movil')
            guardados +=1
        except Exception as e:
            cursor.execute('ROLLBACK TO SAVEPOINT consumo_movil')
            cursor.execute('RELEASE SAVEPOINT consumo_movil')
            if not isinstance(e, ValueError):
                app.logger.exception('Error al guardar consumo móvil en la fila %s', i + 1)
            detalle = str(e) if isinstance(e, ValueError) else 'No se pudo guardar el consumo; revisa los datos e inténtalo nuevamente.'
            errores.append(f'Registro {i +1}: {detalle}')

    try:
        if guardados >0:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        return jsonify({'success':False,'message':'No se pudo confirmar el lote. No se guardaron consumos.'}),503
    finally:
        conn.close()
    if guardados ==0 and errores :
        return jsonify ({"success":False ,"message":"No se guardó ningún registro. Errores: "+"; ".join (errores )})
    return jsonify ({"success":True ,"guardados":guardados ,"errores":errores })

@app .route ("/combustible/fijo")
def combustible_fijo ():
    if 'user_id'not in session :return redirect ("/")
    return render_template ("combustible_fijo.html")

@app .route ("/api/combustible/fijo",methods =["POST"])
def api_combustible_fijo ():
    if 'user_id'not in session :return redirect ("/")
    empresa =session .get ('empresa')
    combustible =request .form .get ('combustible','')
    unidad_consumo =request .form .get ('unidad_consumo','')
    unidad =request .form .get ('unidad','litros')
    periodo =request .form .get ('periodo','')
    uso_final =request .form .get ('uso_final','')
    conn =get_db ()
    cursor =conn .cursor ()
    try:
        cantidad =numero_finito(request.form.get('cantidad'), 'La cantidad', positivo=True)
        costo =numero_finito(request.form.get('costo') or 0, 'El costo')
        fecha =fecha_periodo(periodo).isoformat()
        categoria, factor =resolver_factor_carbono(cursor, combustible, unidad, int(periodo[:4]))
        emision =round(cantidad * factor,4)
        cursor.execute("""
        INSERT INTO registros (fecha, empresa, fuente, categoria, actividad, identificador, unidad, cantidad, costo, factor, emision, alcance)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,(fecha ,empresa ,'Combustión Fija',categoria ,uso_final or 'Combustión estacionaria',
        unidad_consumo ,unidad ,cantidad ,costo ,factor ,emision ,'Alcance 1'))
        registro_id = cursor.fetchone()[0]
        calcular_huella_suministro_registro(cursor, registro_id)
        conn.commit()
        flash("Registro de combustible fijo guardado correctamente.","success")
    except Exception as exc:
        conn.rollback()
        flash(str(exc) if isinstance(exc,ValueError) else 'No se pudo guardar el consumo. No se guardaron cambios.', 'error')
    finally:
        conn.close()
    return redirect (url_for ('combustible_fijo'))

@app .route ("/mis_datos")
def mis_datos ():
    if 'user_id'not in session :return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("SELECT * FROM usuarios WHERE id = %s",(session ['user_id'],))
    datos =cursor .fetchone ()
    cursor .execute ("SELECT DISTINCT EXTRACT(YEAR FROM fecha::date)::int AS anio FROM registros WHERE empresa = %s ORDER BY anio DESC",(session ['empresa'],))
    anios =[r ['anio']for r in cursor .fetchall ()]
    conn .close ()
    return render_template ("mis_datos.html",datos_usuario =datos ,anios =anios )


@app .route ("/mi_cuenta/guardar_preferencias",methods =["POST"])
def guardar_preferencias ():
    if 'user_id'not in session :return redirect ("/")
    anio_default =request .form .get ("anio_default","")
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("UPDATE usuarios SET anio_default = %s WHERE id = %s",(anio_default or None ,session ['user_id']))
    conn .commit ()
    conn .close ()
    flash ("Preferencias guardadas correctamente.","success")
    return redirect (url_for ('mis_datos'))

@app .route ("/cambiar_password",methods =["POST"])
def cambiar_password ():
    if 'user_id'not in session :
        return redirect ("/")
    actual =request .form .get ("password_actual")
    nueva =request .form .get ("password_nueva")
    confirmar =request .form .get ("password_confirmar")
    if nueva !=confirmar :
        flash ("Las contraseñas nuevas no coinciden.","error")
        return redirect (url_for ('mis_datos'))
    if len (nueva )<6 :
        flash ("La contraseña debe tener al menos 6 caracteres.","error")
        return redirect (url_for ('mis_datos'))
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("SELECT password FROM usuarios WHERE id = %s",(session ['user_id'],))
    row =cursor .fetchone ()
    if not row or not verify_password (actual ,row [0 ]):
        conn .close ()
        flash ("La contraseña actual es incorrecta.","error")
        return redirect (url_for ('mis_datos'))
    cursor .execute ("UPDATE usuarios SET password = %s WHERE id = %s",(hash_password (nueva ),session ['user_id']))
    conn .commit ()
    conn .close ()
    flash ("Contraseña actualizada correctamente.","success")
    return redirect (url_for ('mis_datos'))

@app .route ("/configuracion_sistema")
def configuracion_sistema ():return redirect (url_for ('configuracion'))

@app .route ("/configuracion",methods =["GET","POST"])
def configuracion ():
    if 'user_id'not in session :
        return redirect ("/")
    if session .get ('es_admin')==1 :
        return redirect (url_for ('admin_dashboard'))

    empresa =session .get ('empresa')
    current_year =datetime .now ().year 
    anio_sel =request .args .get ('anio','').strip ()
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )

    if request .method =="POST":
        frecuencia = request.form.get('medida_frecuencia', 'anual').strip()
        mensual_payload = request.form.get('mensual_payload', '').strip()
        if mensual_payload:
            try:
                payload_rows = json.loads(mensual_payload)
            except (TypeError, ValueError):
                raise ValueError('La tabla mensual no tiene un formato válido.')
            if not isinstance(payload_rows, list):
                raise ValueError('La tabla mensual no tiene un formato válido.')
            frecuencia = 'mensual'
        else:
            payload_rows = []
        # Las filas mensuales son la fuente de verdad aunque un cliente antiguo
        # envíe el campo oculto con frecuencia anual.
        if request.form.getlist('mensual_periodo[]'):
            frecuencia = 'mensual'
        periodo_medida = (request.form.get('medida_periodo') or '').strip()
        if not periodo_medida:
            periodo_medida = (request.form.get("medida_anio", str(current_year))).strip() or str(current_year)
        if frecuencia == 'anual' and '-' in periodo_medida:
            periodo_medida = periodo_medida[:4]
        anio_sel = periodo_medida[:4]
        try :
            if frecuencia == 'mensual':
                periodos = [str(row.get('periodo') or '').strip() for row in payload_rows] if payload_rows else [p.strip() for p in request.form.getlist('mensual_periodo[]') if p.strip()]
                unidades = [str(row.get('unidad') or '') for row in payload_rows] if payload_rows else request.form.getlist('mensual_unidad[]')
                valores = [str(row.get('valor') or '') for row in payload_rows] if payload_rows else request.form.getlist('mensual_valor[]')
                if not periodos:
                    raise ValueError('Genera al menos una fila mensual antes de guardar.')
                if not (len(periodos) == len(unidades) == len(valores)):
                    raise ValueError('La tabla mensual no coincide con los períodos seleccionados.')
                filas_medida = []
                for periodo, unidad, valor_raw in zip(periodos, unidades, valores):
                    if not re.fullmatch(r'\d{4}-\d{2}', periodo):
                        raise ValueError('Cada mes debe tener formato año-mes.')
                    valor = numero_finito(valor_raw, 'La medida productiva', positivo=True)
                    if not unidad.strip():
                        raise ValueError('Selecciona una unidad para cada mes.')
                    filas_medida.append((periodo, unidad.strip(), valor))
            else:
                unidad_medida = request.form.get("medida_unidad", "").strip()
                valor_raw = (request.form.get('medida_valor') or '').strip()
                valor_medida = numero_finito(valor_raw, 'La medida productiva', positivo=True) if valor_raw or unidad_medida else None
                filas_medida = [(periodo_medida, unidad_medida, valor_medida)] if valor_medida is not None else []

            cambios = []
            for periodo, unidad, valor in filas_medida:
                anio_medida = int(periodo[:4])
                if not 1 <= anio_medida <= 9999:
                    raise ValueError('El año de la medida productiva no es válido.')
                cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (f'medida:{empresa}:{periodo}',))
                cursor.execute('SELECT unidad, valor FROM medidas_productivas WHERE empresa=%s AND periodo=%s FOR UPDATE',
                    (empresa, periodo))
                anterior = cursor.fetchone()
                cambio = not anterior or anterior['unidad'] != unidad or float(anterior['valor']) != valor
                if cambio:
                    cambios.append((periodo, unidad, valor, anterior))
            if cambios and any(anterior for _, _, _, anterior in cambios) and request.form.get('confirmar_medida') != '1':
                raise ValueError('Confirma el valor anterior y el nuevo antes de reemplazar las medidas productivas.')
            if cambios and any(anterior for _, _, _, anterior in cambios) and frecuencia == 'anual':
                confirmado_unidad = request.form.get('medida_anterior_unidad', '').strip()
                confirmado_valor_raw = request.form.get('medida_anterior_valor', '').strip()
                try:
                    confirmado_valor = float(confirmado_valor_raw)
                except (TypeError, ValueError):
                    confirmado_valor = None
                for _, _, _, anterior in cambios:
                    if (
                        confirmado_valor is None
                        or confirmado_unidad != str(anterior['unidad'] or '').strip()
                        or confirmado_valor != float(anterior['valor'])
                    ):
                        raise ValueError('La confirmación no coincide con el valor actualmente guardado.')
            # Compatibilidad con clientes antiguos que aún envíen estas preguntas.
            if any(campo in request.form for campo in ('info_unidad', 'vehiculos', 'colaboradores', 'tarjeta', 'energeticos[]')):
                info_por_unidad = 1 if request.form.get('info_unidad') else 0
                vehiculos = 1 if request.form.get('vehiculos', 'no') == 'si' else 0
                combustible_colaboradores = 1 if request.form.get('colaboradores', 'no') == 'si' else 0
                tarjeta_combustible = 1 if request.form.get('tarjeta', 'no') == 'si' else 0
                energeticos_sel = request.form.getlist('energeticos[]')
                cursor.execute("""
                    INSERT INTO configuracion (empresa, info_por_unidad, vehiculos, combustible_colaboradores, tarjeta_combustible)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (empresa) DO UPDATE SET
                        info_por_unidad = EXCLUDED.info_por_unidad,
                        vehiculos = EXCLUDED.vehiculos,
                        combustible_colaboradores = EXCLUDED.combustible_colaboradores,
                        tarjeta_combustible = EXCLUDED.tarjeta_combustible
                """, (empresa, info_por_unidad, vehiculos, combustible_colaboradores, tarjeta_combustible))
                cursor.execute('DELETE FROM energeticos_empresa WHERE empresa = %s', (empresa,))
                for energetico in energeticos_sel:
                    proveedor = request.form.get(f'proveedor_{energetico}', '').strip()
                    cursor.execute("""
                        INSERT INTO energeticos_empresa (empresa, energetico, proveedor, documento)
                        VALUES (%s, %s, %s, %s)
                    """, (empresa, energetico, proveedor, None))

            for periodo, unidad, valor, _ in cambios:
                guardar_medida_productiva(cursor, empresa, periodo, unidad, valor)
            conn .commit ()
            flash ("Configuración guardada correctamente.","success")
        except Exception as e :
            conn .rollback ()
            flash (f"No se pudo guardar la configuración: {e }","error")
        finally :
            conn .close ()
        periodo_redireccion = filas_medida[0][0] if frecuencia == 'mensual' and filas_medida else periodo_medida
        return redirect(url_for('configuracion', anio=anio_sel,
                                periodo=periodo_redireccion if frecuencia == 'mensual' else None,
                                vista=frecuencia))

    cursor .execute ("SELECT * FROM configuracion WHERE empresa = %s",(empresa ,))
    configuracion_row =cursor .fetchone ()
    cursor .execute ("SELECT energetico, proveedor FROM energeticos_empresa WHERE empresa = %s",(empresa ,))
    energeticos_guardados =[dict (r )for r in cursor .fetchall ()]
    cursor .execute ("SELECT DISTINCT anio FROM medidas_productivas WHERE empresa = %s ORDER BY anio DESC",(empresa ,))
    anios_medidas =[r [0 ]for r in cursor .fetchall ()]
    if current_year not in anios_medidas :
        anios_medidas =[current_year ]+anios_medidas 
    if anio_sel :
        try :
            anio_sel =int (anio_sel )
        except Exception :
            anio_sel =current_year 
    else :
        cursor .execute ("SELECT anio FROM medidas_productivas WHERE empresa = %s ORDER BY anio DESC, id DESC LIMIT 1",(empresa ,))
        row_anio =cursor .fetchone ()
        anio_sel =int (row_anio [0 ])if row_anio else current_year 
    periodo_sel = request.args.get('periodo', '').strip() or str(anio_sel)
    vista_inicial = request.args.get('vista', 'anual').strip()
    if vista_inicial not in ('anual', 'mensual'):
        vista_inicial = 'anual'
    if len(periodo_sel) == 7:
        vista_inicial = 'mensual'
    cursor .execute ("SELECT anio, periodo, unidad, valor, fecha_actualizacion FROM medidas_productivas WHERE empresa = %s AND periodo = %s",(empresa ,periodo_sel ))
    medida_actual =cursor .fetchone ()
    if medida_actual :
        medida_actual =dict (medida_actual )
        total_t ,intensidad =calcular_intensidad_emisiones (conn ,empresa ,medida_actual.get('periodo') or anio_sel ,medida_actual .get ('valor',0 ))
        medida_actual ['emisiones_anio_tco2e']=total_t 
        medida_actual ['intensidad_emisiones']=intensidad 
    medidas_historial =obtener_medidas_productivas (conn ,empresa )
    medidas_anuales = [m for m in medidas_historial if len(str(m.get('periodo') or m.get('anio') or '')) == 4]
    cursor .execute ("SELECT sector_empresa, tipo_empresa FROM usuarios WHERE empresa = %s",(empresa ,))
    perfil_empresa =cursor .fetchone ()
    cursor.execute("""
        SELECT TO_CHAR(fecha::date, 'YYYY-MM') AS periodo,
               SUBSTRING(fecha::text, 1, 7) AS periodo_texto,
               COALESCE(SUM(emision), 0) AS emisiones_kg
        FROM registros
        WHERE empresa = %s AND fecha IS NOT NULL
        GROUP BY TO_CHAR(fecha::date, 'YYYY-MM'), SUBSTRING(fecha::text, 1, 7)
    """, (empresa,))
    emisiones_mensuales = {}
    for row in cursor.fetchall():
        emisiones = float(row['emisiones_kg'] or 0)
        for clave in (row['periodo'], row['periodo_texto']):
            if clave:
                emisiones_mensuales[str(clave)] = emisiones
    conn .close ()

    energeticos_seleccionados =[r ['energetico']for r in energeticos_guardados ]
    proveedores_map ={r ['energetico']:r ['proveedor']or ''for r in energeticos_guardados }

    return render_template (
    "configuracion.html",
    empresa =empresa ,
    configuracion =configuracion_row ,
    energeticos_seleccionados =energeticos_seleccionados ,
    proveedores_map =proveedores_map ,
    medida_actual =medida_actual ,
    medidas_historial =medidas_historial ,
    medidas_anuales =medidas_anuales ,
    anios_medidas =sorted (set (anios_medidas ),reverse =True ),
    anio_seleccionado =anio_sel ,
    periodo_seleccionado = periodo_sel,
    vista_inicial = vista_inicial,
    emisiones_mensuales = emisiones_mensuales,
    perfil_empresa =perfil_empresa ,
    unidades_productivas =UNIDADES_PRODUCTIVAS 
    )

@app .route ("/agua/registro",methods =["GET","POST"])
def agua_registro ():
    if 'user_id'not in session :
        return redirect ("/")
    return redirect(url_for('registro',tab='agua'))
@app .route ("/agua/reporte")
def agua_reporte ():
    if 'user_id'not in session :
        return redirect ("/")
    return redirect(url_for('agua_resultados'))


@app .route ("/agua/registrar",methods =["GET","POST"])
def agua_registrar_datos ():
    if 'user_id'not in session :
        return redirect ("/")
    if request .method =="POST":
        flash ("El registro de agua ahora se realiza desde Nuevo Registro > Agua - Water Footprint.","info")
    return redirect (url_for ("registro",tab ="agua"))


@app .route ("/agua/retornos-reuso",methods =["GET","POST"])
def agua_retorno_reuso ():
    if 'user_id'not in session :
        return redirect ("/")
    empresa =session .get ("empresa")
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    sede_predeterminada =_obtener_sede_activa_predeterminada (cursor ,empresa )

    if request .method =="POST":
        periodo_raw =request .form .get ("periodo")or ""
        try :
            periodo =fecha_periodo(periodo_raw)
        except ValueError :
            conn .close ()
            flash ("Debes seleccionar un mes de registro válido.","error")
            return render_template ("agua_retorno_reuso.html",sede_predeterminada =sede_predeterminada )

        tipo_flujo =request .form .get ("tipo_flujo","retorno")
        if tipo_flujo not in ("retorno","reuso"):
            conn .close ()
            flash ("El tipo de flujo no es válido.","error")
            return render_template ("agua_retorno_reuso.html",sede_predeterminada =sede_predeterminada )

        try:
            volumen_agua =numero_finito(request.form.get('volumen_m3'),'El volumen de agua')
        except ValueError as exc:
            conn .close ()
            flash (str(exc),"error")
            return render_template ("agua_retorno_reuso.html",sede_predeterminada =sede_predeterminada )

        valor_retorno =request .form .get ("retorna_mismo_sistema_hidrico","no_informado")
        if valor_retorno not in ('si','no','no_informado') or request.form.get('tiene_tratamiento','no_informado') not in ('si','no','no_informado'):
            conn.close()
            flash('La información del retorno o tratamiento no es válida.','error')
            return redirect(url_for('agua_retorno_reuso'))
        retorna_mismo =None if valor_retorno =="no_informado"else valor_retorno =="si"
        valor_tratamiento =request .form .get ("tiene_tratamiento","no_informado")
        tiene_tratamiento =None if valor_tratamiento =="no_informado"else valor_tratamiento =="si"
        destino_agua =request .form .get ("destino_agua")if tipo_flujo =="retorno"else request .form .get ("destino_agua")

        try :
            _guardar_flujo_agua (
            cursor ,
            empresa ,
            sede_predeterminada ["id"]if sede_predeterminada else None ,
            periodo ,
            tipo_flujo ,
            None ,
            destino_agua ,
            volumen_agua ,
            request .form .get ("proceso_o_area"),
            retorna_mismo ,
            tiene_tratamiento ,
            request .form .get ("calidad_dato"),
            request .form .get ("evidencia"),
            request .form .get ("observaciones"),
            )
            conn .commit ()
            flash ("Flujo de agua guardado exitosamente.","success")
            conn .close ()
            return redirect (url_for ("agua_dashboard"))
        except Exception as exc :
            conn .rollback ()
            conn .close ()
            flash (f"No se pudo guardar el flujo de agua: {exc }","error")
            return render_template ("agua_retorno_reuso.html",sede_predeterminada =sede_predeterminada )

    conn .close ()
    return render_template ("agua_retorno_reuso.html",sede_predeterminada =sede_predeterminada )


def _agua_filtros_resultados():
    vista = request.args.get('vista','mensual')
    if vista not in ('mensual','anual'):
        raise ValueError('Selecciona una vista mensual o anual.')
    periodo = normalizar_periodo(request.args.get('periodo'))
    return (periodo[:4] if periodo and vista=='anual' else periodo),vista


@app .route ("/agua/registros")
@app .route ("/agua/resultados")
def agua_resultados ():
    if 'user_id'not in session :
        return redirect ("/")
    empresa =session .get ("empresa")
    try:
        periodo,vista =_agua_filtros_resultados()
    except ValueError as exc:
        flash(str(exc),'error')
        return redirect(url_for('agua_resultados'))
    conn =get_db ()
    data =_agua_calcular_resumen (conn ,empresa ,periodo )
    resultados =_agua_agrupar_resultados_reportes (data ["flujos"],data ["sedes"],data ["factores"],medida_valor =data['medidas_por_anio'] ,vista =vista )
    if data['estado_medicion_agua'] != 'ambos':
        for resultado in resultados:
            resultado.update(consumo_operativo_m3=None,huella_escasez_m3eq=None,
                intensidad_hidrica=None,intensidad_escasez=None,nivel_calculo='Datos insuficientes')
    conn .close ()
    return render_template ("agua_resultados.html",empresa =empresa ,periodo =periodo ,resultados =resultados ,vista =vista ,**data ,menu_activo ="registros")

@app .route ("/agua/registros/eliminar",methods =["POST"])
def agua_eliminar_registros ():
    if 'user_id'not in session :
        return redirect ("/")
    empresa =session .get ("empresa")
    sede_id_raw =request .form .get ("sede_id")
    periodo_grupo =request .form .get ("periodo")or ""
    vista =request .form .get ("vista","mensual")
    sede_id =int (sede_id_raw )if sede_id_raw not in (None ,"","None")else None 
    if not periodo_grupo :
        flash ("No se pudo identificar el periodo del registro de agua.","warning")
        return redirect (url_for ("agua_resultados",vista =vista ))
    conn =get_db ()
    cursor =conn .cursor ()
    try :
        if vista =="anual":
            cursor .execute (
            """
            DELETE FROM agua_flujos
            WHERE empresa = %s
              AND sede_id IS NOT DISTINCT FROM %s
              AND SUBSTRING(periodo::text, 1, 4) = %s
            """,
            (empresa ,sede_id ,periodo_grupo [:4 ]),
            )
            eliminados =cursor .rowcount
            cursor .execute (
            """
            DELETE FROM resultados_huella_agua
            WHERE empresa = %s
              AND sede_id IS NOT DISTINCT FROM %s
              AND SUBSTRING(periodo::text, 1, 4) = %s
            """,
            (empresa ,sede_id ,periodo_grupo [:4 ]),
            )
        else :
            cursor .execute (
            """
            DELETE FROM agua_flujos
            WHERE empresa = %s
              AND sede_id IS NOT DISTINCT FROM %s
              AND to_char(periodo, 'YYYY-MM') = %s
            """,
            (empresa ,sede_id ,periodo_grupo [:7 ]),
            )
            eliminados =cursor .rowcount
            cursor .execute (
            """
            DELETE FROM resultados_huella_agua
            WHERE empresa = %s
              AND sede_id IS NOT DISTINCT FROM %s
              AND to_char(periodo, 'YYYY-MM') = %s
            """,
            (empresa ,sede_id ,periodo_grupo [:7 ]),
            )
        conn .commit ()
        flash (f"Se eliminaron {eliminados } registro(s) de agua del periodo seleccionado.","success")
    except Exception as exc :
        conn .rollback ()
        flash (f"No se pudieron eliminar los registros de agua: {exc }","error")
    finally :
        conn .close ()
    return redirect (url_for ("agua_resultados",vista =vista ))


@app .route ("/agua/metodologia")
def agua_metodologia ():
    if 'user_id'not in session :
        return redirect ("/")
    return render_template ("agua_metodologia.html",menu_activo ="metodologia")


@app .route ("/agua/resultados/descargar")
def agua_resultados_descargar ():
    if 'user_id'not in session :
        return redirect ("/")
    empresa =session .get ("empresa")
    try:
        periodo,vista =_agua_filtros_resultados()
    except ValueError as exc:
        flash(str(exc),'error')
        return redirect(url_for('agua_resultados'))
    conn =get_db ()
    data =_agua_calcular_resumen (conn ,empresa ,periodo )
    reporte =construir_reporte_huella(empresa,periodo or vista,data['flujos'],data['sedes'],data['factores'],
        medida_productiva=data['medidas_por_anio'],vista=vista,diagnostico=data['estado_medicion_agua'] or 'sin_medicion',
        suministro_m3=data['huella_suministro_total'],suministro_completo=data['suministro_completo'],
        suministro_disponible=data['suministro_disponible'])
    df_resumen =pd .DataFrame (reporte ["Resumen"])
    df_flujos =pd .DataFrame (reporte ["Flujos de agua"])
    df_resultados =pd .DataFrame (reporte ["Resultados por sede"])
    df_intensidades =pd .DataFrame (reporte ["Intensidades"])
    df_factores =pd .DataFrame (reporte ["Factores de escasez aplicados"])
    df_cobertura =pd .DataFrame (reporte ["Cobertura y calidad de datos"])
    df_metodologia =pd .DataFrame (reporte ["Metodología y supuestos"])
    sedes_incluidas =[s ["nombre_sede"]for s in data ["sedes"]]

    output =io .BytesIO ()
    with pd .ExcelWriter (output ,engine ="xlsxwriter")as writer :
        df_resumen .to_excel (writer ,index =False ,sheet_name ="Resumen")
        df_flujos .to_excel (writer ,index =False ,sheet_name ="Flujos de agua")
        df_resultados .to_excel (writer ,index =False ,sheet_name ="Resultados por sede")
        df_intensidades .to_excel (writer ,index =False ,sheet_name ="Intensidades")
        df_factores .to_excel (writer ,index =False ,sheet_name ="Factores de escasez")
        df_cobertura .to_excel (writer ,index =False ,sheet_name ="Cobertura y calidad")
        df_metodologia .to_excel (writer ,index =False ,sheet_name ="Metodología y supuestos")
        workbook =writer .book 
        money_fmt =workbook .add_format ({"num_format":"0.000000"})
        eq_fmt =workbook .add_format ({"num_format":"0.000000"})
        text_fmt =workbook .add_format ({"text_wrap":True })
        for sheet_name in ["Resumen","Flujos de agua","Resultados por sede","Intensidades","Factores de escasez","Cobertura y calidad","Metodología y supuestos"]:
            ws =writer .sheets [sheet_name ]
            ws .set_column (0 ,0 ,26 )
            ws .set_column (1 ,6 ,22 )
            ws .set_column (7 ,20 ,18 )
            ws .freeze_panes (1 ,0 )
        if not df_resumen .empty :
            ws =writer .sheets ["Resumen"]
            ws .set_column (0 ,0 ,36 )
            ws .set_column (1 ,1 ,16 )
            ws .set_column (2 ,2 ,22 ,money_fmt )
        if not df_flujos .empty :
            ws =writer .sheets ["Flujos de agua"]
            ws .set_column (0 ,len (df_flujos .columns )-1 ,18 )
        if not df_resultados .empty :
            ws =writer .sheets ["Resultados por sede"]
            ws .set_column (0 ,len (df_resultados .columns )-1 ,18 )
        if not df_metodologia .empty :
            ws =writer .sheets ["Metodología y supuestos"]
            ws .set_column (0 ,0 ,24 )
            ws .set_column (1 ,1 ,120 ,text_fmt )
    output .seek (0 )
    conn .close ()
    nombre =f"huella_hidrica_{empresa }_{periodo or vista }.xlsx".replace (" ","_")
    return send_file (output ,download_name =nombre ,as_attachment =True )
@app .route ("/residuos/registro",methods =["GET","POST"])
def residuos_registro ():return render_template ("residuos_registro.html")
@app .route ("/residuos/reporte")
def residuos_reporte ():return render_template ("residuos_reporte.html")
@app .route ("/alcance_3")
def alcance_3 ():return redirect (url_for ('formulario_residuos'))

# ================= ELIMINACIÓN UNIVERSAL =================
@app .route ('/eliminar_cualquier_registro/<tipo>/<int:id>')
def eliminar_cualquier_registro (tipo ,id ):
    if 'user_id'not in session :return redirect ("/")
    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor ()
    try :
        if tipo =='vehiculo':
            eliminado = eliminar_consumo_movil(cursor, empresa, id)
        else :
            eliminado = eliminar_consumo(cursor, empresa, id)
        conn .commit ()
        flash ("Registro eliminado con éxito." if eliminado else "No se encontró el registro.", "success" if eliminado else "warning")
    except Exception:
        conn.rollback()
        flash ("No se pudo eliminar el registro. No se guardaron cambios.","danger")
    finally :
        conn .close ()
        # Magia: Te devuelve a la misma pantalla donde hiciste clic en el basurero
    return redirect (request .referrer or url_for ('dashboard'))

    # ================= HISTORIAL DE CARGAS PDF =================
@app .route ("/residuos/pdf_historial")
def pdf_historial ():
    if 'user_id'not in session :return redirect ("/")
    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("""
        SELECT id, fecha_subida, nombre_archivo, tipo, registros_generados, sin_factor
        FROM pdf_uploads WHERE empresa = %s ORDER BY fecha_subida DESC
    """,(empresa ,))
    cargas =[dict (r )for r in cursor .fetchall ()]
    conn .close ()
    return render_template ("pdf_historial.html",cargas =cargas )


    # ================= HISTORIAL PDF GLOBAL (ADMIN) =================
@app .route ("/admin/pdf_historial")
def admin_pdf_historial ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    POR_PAGINA =50 
    pagina =max (1 ,int (request .args .get ('pagina',1 )))
    offset =(pagina -1 )*POR_PAGINA 
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("SELECT COUNT(*) FROM pdf_uploads")
    total =cursor .fetchone ()[0 ]
    total_paginas =max (1 ,(total +POR_PAGINA -1 )//POR_PAGINA )
    cursor .execute ("""
        SELECT empresa, fecha_subida, nombre_archivo, tipo, registros_generados, sin_factor
        FROM pdf_uploads ORDER BY fecha_subida DESC LIMIT %s OFFSET %s
    """,(POR_PAGINA ,offset ))
    cargas =[dict (r )for r in cursor .fetchall ()]
    conn .close ()
    return render_template ("admin_pdf_historial.html",cargas =cargas ,
    pagina =pagina ,total_paginas =total_paginas ,total =total )


    # ================= VALIDACIÓN DE PDFs (ADMIN) =================
@app .route ("/admin/pendientes")
def admin_pendientes ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("SELECT * FROM pending_pdf_uploads ORDER BY CASE estado WHEN 'pendiente' THEN 0 ELSE 1 END, fecha_subida DESC")
    envios =[dict (r )for r in cursor .fetchall ()]
    conn .close ()
    for e in envios :
        try :
            e ['filas']=json .loads (e ['datos_json']or '[]')
        except Exception :
            e ['filas']=[]
    return render_template ("admin_pendientes.html",envios =envios )

@app .route ("/admin/aprobar_pdf/<int:id>",methods =["POST"])
def admin_aprobar_pdf (id ):
    if session .get ('es_admin')!=1 :return redirect ("/")
    from datetime import datetime as dt 
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("SELECT * FROM pending_pdf_uploads WHERE id = %s",(id ,))
    pending =cursor .fetchone ()
    if not pending :
        flash ("Envío no encontrado.","danger")
        conn .close ()
        return redirect (url_for ('admin_pendientes'))
    try :
        filas =json .loads (pending ['datos_json']or '[]')
        empresa =pending ['empresa']
        for fila in filas :
            cursor .execute ("""
                INSERT INTO registros (
                    fecha, empresa, area, alcance, fuente, categoria, actividad,
                    unidad, cantidad, factor, emision, codigo_residuo,
                    residuo_oficial, tratamiento_normalizado, categoria_defra,
                    tratamiento_defra, estado_validacion, observacion_factor
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,(fila ['fecha'],empresa ,
            (fila .get ('destino')or 'Operaciones')[:50 ],
            'Alcance 3','Residuos',fila ['categoria'],fila .get ('tratamiento',''),
            'kg',fila ['cantidad'],fila ['factor'],fila ['emision'],
            fila.get('codigo_residuo',''),fila.get('categoria',''),
            normalizar_tratamiento_residuo(fila.get('tratamiento','')),
            fila.get('defra_cat',''),fila.get('tratamiento_defra',''),
            fila.get('estado_validacion', 'calculado' if fila.get('factor', 0) > 0 else 'requiere_revision'),
            fila.get('observacion', '')))
        con_factor =sum (1 for f in filas if f ['factor']>0 )
        sin_factor =len (filas )-con_factor 
        cursor .execute ("""
            INSERT INTO pdf_uploads (empresa, fecha_subida, nombre_archivo, tipo, registros_generados, sin_factor)
            VALUES (%s, %s, %s, %s, %s, %s)
        """,(empresa ,dt .now ().strftime ("%Y-%m-%d %H:%M"),pending ['nombre_archivo'],pending ['tipo'],len (filas ),sin_factor ))
        cursor .execute ("""
            UPDATE pending_pdf_uploads SET estado = 'aprobado', fecha_revision = %s WHERE id = %s
        """,(dt .now ().strftime ("%Y-%m-%d %H:%M"),id ))
        conn .commit ()
        flash (f"Aprobado: {len (filas )} registros guardados para {empresa }.","success")
    except Exception as e :
        conn .rollback ()
        flash (f"Error al aprobar: {str (e )}","danger")
    finally :
        conn .close ()
    return redirect (url_for ('admin_pendientes'))

@app .route ("/admin/rechazar_pdf/<int:id>",methods =["POST"])
def admin_rechazar_pdf (id ):
    if session .get ('es_admin')!=1 :return redirect ("/")
    from datetime import datetime as dt 
    motivo =request .form .get ('motivo','').strip ()or 'Sin motivo especificado'
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("""
        UPDATE pending_pdf_uploads SET estado = 'rechazado', motivo_rechazo = %s, fecha_revision = %s WHERE id = %s
    """,(motivo ,dt .now ().strftime ("%Y-%m-%d %H:%M"),id ))
    conn .commit ()
    conn .close ()
    flash ("Envío rechazado.","warning")
    return redirect (url_for ('admin_pendientes'))

    # ================= MIS ENVÍOS (USUARIO) =================
@app .route ("/mis_envios")
def mis_envios ():
    if 'user_id'not in session or session .get ('es_admin')==1 :return redirect ("/")
    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("SELECT * FROM pending_pdf_uploads WHERE empresa = %s ORDER BY fecha_subida DESC",(empresa ,))
    envios =[dict (r )for r in cursor .fetchall ()]
    conn .close ()
    for e in envios :
        try :
            e ['filas']=json .loads (e ['datos_json']or '[]')
        except Exception :
            e ['filas']=[]
    return render_template ("mis_envios.html",envios =envios )


    # ================= API GHG GLOBAL (ADMIN) =================
@app .route ("/api/admin/emisiones-por-empresa")
def api_admin_emisiones_por_empresa ():
    auth_header =request .headers .get ("Authorization","")
    token =auth_header [7 :].strip ()if auth_header .startswith ("Bearer ")else ""
    claims =verify_api_token (token )
    if not claims :
        return jsonify ({"error":"Token inválido o expirado"}),401 
    if int (claims .get ("es_admin")or 0 )!=1 :
        return jsonify ({"error":"No autorizado"}),403 

    conn =None 
    try :
        anio =request .args .get ('anio')
        anio =anio .strip ()if anio else None 
        filtro_anio ="WHERE SUBSTRING(fecha::text,1,4) = %s"if anio else ""

        query =f"""
            WITH base AS (
                SELECT
                    empresa,
                    SUBSTRING(fecha::text,1,4) AS anio,
                    CASE
                        WHEN fuente IN ('Combustión Fija','Combustible Móvil','Combustión Estacionaria','Refrigerantes','Fugas de Refrigerantes') THEN 'Alcance 1'
                        WHEN fuente = 'Electricidad' THEN 'Alcance 2'
                        WHEN fuente = 'Residuos' THEN 'Alcance 3'
                        ELSE COALESCE(alcance,'')
                    END AS alcance_calc,
                    COALESCE(emision, 0) AS emision,
                    COALESCE(emision_ubicacion, 0) AS emision_ubicacion
                FROM registros
                {filtro_anio }
            )
            SELECT
                empresa,
                anio,
                ROUND(COALESCE(SUM(CASE WHEN alcance_calc = 'Alcance 1' THEN emision ELSE 0 END), 0)::numeric, 2) AS alcance_1_kgco2e,
                ROUND(COALESCE(SUM(CASE WHEN alcance_calc = 'Alcance 2' THEN emision ELSE 0 END), 0)::numeric, 2) AS alcance_2_mercado_kgco2e,
                ROUND(COALESCE(SUM(CASE WHEN alcance_calc = 'Alcance 2' THEN emision_ubicacion ELSE 0 END), 0)::numeric, 2) AS alcance_2_ubicacion_kgco2e,
                ROUND(COALESCE(SUM(CASE WHEN alcance_calc = 'Alcance 3' THEN emision ELSE 0 END), 0)::numeric, 2) AS alcance_3_kgco2e,
                ROUND((
                    COALESCE(SUM(CASE WHEN alcance_calc = 'Alcance 1' THEN emision ELSE 0 END), 0)
                    + COALESCE(SUM(CASE WHEN alcance_calc = 'Alcance 2' THEN emision ELSE 0 END), 0)
                    + COALESCE(SUM(CASE WHEN alcance_calc = 'Alcance 3' THEN emision ELSE 0 END), 0)
                )::numeric, 2) AS total_ghg_kgco2e
            FROM base
            GROUP BY empresa, anio
            ORDER BY empresa, anio
        """

        conn =get_db ()
        cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
        if anio :
            cursor .execute (query ,(anio ,))
        else :
            cursor .execute (query )

        filas =cursor .fetchall ()
        empresas_respuesta =[fila ["empresa"]for fila in filas if fila ["empresa"]]
        perfil_empresa_map ={}
        medidas_empresa_map ={}

        if empresas_respuesta :
            cursor .execute (
            """
                    SELECT empresa, sector_empresa, tipo_empresa
                    FROM usuarios
                    WHERE empresa = ANY(%s)
                """,
            (empresas_respuesta ,)
            )
            for row in cursor .fetchall ():
                perfil_empresa_map [row ["empresa"]]={
                "sector_empresa":row ["sector_empresa"],
                "tipo_empresa":row ["tipo_empresa"],
                }

            cursor .execute (
            """
                    SELECT empresa, anio, periodo, unidad, valor, fecha_actualizacion
                    FROM medidas_productivas
                    WHERE empresa = ANY(%s) AND periodo = anio::text
                    ORDER BY empresa, anio DESC, id DESC
                """,
            (empresas_respuesta ,)
            )
            for row in cursor .fetchall ():
                empresa_row =row ["empresa"]
                total_t ,intensidad =calcular_intensidad_emisiones (conn ,empresa_row ,row ["anio"],row .get ("valor")or 0 )
                cur_total=conn.cursor()
                cur_total.execute('SELECT COALESCE(SUM(emision),0) FROM registros WHERE empresa=%s AND SUBSTRING(fecha::text,1,4)=%s',(empresa_row,str(row['anio'])))
                total_kg=float(cur_total.fetchone()[0] or 0)
                cur_total.close()
                medidas_empresa_map .setdefault (empresa_row ,[]).append ({
                "anio":int (row ["anio"]or 0 ),
                "unidad":row ["unidad"],
                "valor":round (float (row ["valor"]or 0 ),6 ),
                "fecha_actualizacion":row ["fecha_actualizacion"],
                "emisiones_anio_kgco2e":round(total_kg,2),
                "intensidad_emisiones_kgco2e_unidad":round (float (intensidad or 0 ),6 ),
                })

        respuesta =[]
        for fila in filas :
            empresa =fila ["empresa"]
            medidas_empresa =medidas_empresa_map .get (empresa ,[])
            medida_productiva_anio =next (
            (m for m in medidas_empresa if str (m .get ("anio")or "")==str (fila ["anio"]or "")),
            None 
            )
            perfil_empresa =perfil_empresa_map .get (empresa ,{})
            respuesta .append ({
            "empresa":empresa ,
            "anio":str (fila ["anio"]or ""),
            "sector_empresa":perfil_empresa .get ("sector_empresa"),
            "tipo_empresa":perfil_empresa .get ("tipo_empresa"),
            "alcance_1_kgco2e":round (float (fila ["alcance_1_kgco2e"]or 0 ),2 ),
            "alcance_2_mercado_kgco2e":round (float (fila ["alcance_2_mercado_kgco2e"]or 0 ),2 ),
            "alcance_2_ubicacion_kgco2e":round (float (fila ["alcance_2_ubicacion_kgco2e"]or 0 ),2 ),
            "alcance_3_kgco2e":round (float (fila ["alcance_3_kgco2e"]or 0 ),2 ),
            "total_ghg_kgco2e":round (float (fila ["total_ghg_kgco2e"]or 0 ),2 ),
            "medida_productiva_anio":medida_productiva_anio ,
            "medidas_productivas":medidas_empresa ,
            })

        return jsonify (respuesta )
    except Exception as e :
        return jsonify ({"error":str (e )}),500 
    finally :
        if conn :
            conn .close ()


            # ================= EXPORTACIÓN GHG GLOBAL (ADMIN) =================
@app .route ("/admin/exportar_ghg_global")
def admin_exportar_ghg_global ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    anio =request .args .get ('anio',str (datetime .now ().year ))

    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("SELECT empresa FROM usuarios WHERE es_admin = 0 AND COALESCE(NULLIF(rol,''),'empresa') = 'empresa' ORDER BY empresa")
    empresas =[r [0 ]for r in cursor .fetchall ()]

    filtro =" AND SUBSTRING(fecha::text,1,4) = %s"if anio !='Todos'else ""
    resumen_global =[]
    output =io .BytesIO ()

    with pd .ExcelWriter (output ,engine ='openpyxl')as writer :
        for empresa in empresas :
            p1 =(empresa ,anio )if anio !='Todos'else (empresa ,)

            _case_alc ="""
                CASE
                    WHEN fuente IN ('Combustión Fija','Combustible Móvil','Combustión Estacionaria','Refrigerantes','Fugas de Refrigerantes') THEN 'Alcance 1'
                    WHEN fuente = 'Electricidad' THEN 'Alcance 2'
                    WHEN fuente = 'Residuos' THEN 'Alcance 3'
                    ELSE COALESCE(alcance,'')
                END
            """
            df_a1 =pd .read_sql_query (
            f"SELECT fecha,area,fuente,categoria,actividad,unidad,cantidad,factor,emision "
            f"FROM registros WHERE empresa=%s AND ({_case_alc })='Alcance 1'{filtro } ORDER BY fecha",
            conn ,params =p1 )
            df_a2 =pd .read_sql_query (
            f"SELECT fecha,area,fuente,categoria,actividad,unidad,cantidad,factor,emision,"
            f"COALESCE(emision_ubicacion,0) as emision_ubicacion "
            f"FROM registros WHERE empresa=%s AND ({_case_alc })='Alcance 2'{filtro } ORDER BY fecha",
            conn ,params =p1 )
            df_a3 =pd .read_sql_query (
            f"SELECT fecha,area,fuente,categoria,actividad,unidad,cantidad,factor,emision "
            f"FROM registros WHERE empresa=%s AND ({_case_alc })='Alcance 3'{filtro } ORDER BY fecha",
            conn ,params =p1 )

            total_a1 =float (df_a1 ['emision'].sum ())if not df_a1 .empty else 0.0 
            total_a2 =float (df_a2 ['emision'].sum ())if not df_a2 .empty else 0.0 
            total_a2_ub =float (df_a2 ['emision_ubicacion'].sum ())if not df_a2 .empty else 0.0 
            total_a3 =float (df_a3 ['emision'].sum ())if not df_a3 .empty else 0.0 
            resumen_global .append ({
            "Empresa":empresa ,
            "Alcance 1 (kg CO₂e)":round (total_a1 ,2 ),
            "Alcance 2 mercado (kg CO₂e)":round (total_a2 ,2 ),
            "Alcance 2 ubicación (kg CO₂e)":round (total_a2_ub ,2 ),
            "Alcance 3 (kg CO₂e)":round (total_a3 ,2 ),
            "TOTAL GHG (kg CO₂e)":round (total_a1 +total_a2 +total_a3 ,2 ),
            })

            if df_a1 .empty and df_a2 .empty and df_a3 .empty :
                continue 

            col_map ={
            'fecha':'Fecha','area':'Área','fuente':'Fuente',
            'categoria':'Categoría','actividad':'Actividad',
            'unidad':'Unidad','cantidad':'Cantidad',
            'factor':'Factor de emisión','emision':'Emisión (kg CO₂e)',
            'emision_ubicacion':'Emis. ubicación (kg CO₂e)'
            }
            dfs_empresa =[]
            for alcance_name ,df_alc in [('Alcance 1',df_a1 ),('Alcance 2',df_a2 ),('Alcance 3',df_a3 )]:
                if not df_alc .empty :
                    df_copy =df_alc .copy ()
                    _etiquetar_unidad_factor(df_copy)
                    df_copy .insert (0 ,'Alcance',alcance_name )
                    df_copy .rename (columns ={k :v for k ,v in col_map .items ()if k in df_copy .columns },inplace =True )
                    dfs_empresa .append (df_copy )
            if dfs_empresa :
                nombre_hoja =empresa [:28 ].replace ('/','-').replace ('\\','-').replace ('*','').replace ('?','').replace ('[','').replace (']','').replace (':','')
                pd .concat (dfs_empresa ,ignore_index =True ).to_excel (writer ,sheet_name =nombre_hoja ,index =False )

        df_resumen =pd .DataFrame (resumen_global )
        df_resumen .to_excel (writer ,sheet_name ='Resumen Global',index =False )

        from openpyxl .styles import Font ,PatternFill ,Alignment 
        header_fill =PatternFill ("solid",fgColor ="064E3B")
        header_font =Font (bold =True ,color ="FFFFFF")
        total_fill =PatternFill ("solid",fgColor ="D1FAE5")
        total_font =Font (bold =True )
        for sheet_name ,ws in writer .sheets .items ():
            for cell in ws [1 ]:
                cell .fill =header_fill 
                cell .font =header_font 
                cell .alignment =Alignment (horizontal ='center')
            for col in ws .columns :
                ws .column_dimensions [col [0 ].column_letter ].width =22 
            if sheet_name =='Resumen Global':
                for row_idx in range (2 ,ws .max_row +1 ):
                    for cell in ws [row_idx ]:
                        if cell .column_letter in ('F',):
                            cell .fill =total_fill 
                            cell .font =total_font 

    conn .close ()
    output .seek (0 )
    nombre =f"GHG_Global_{anio }_{datetime .now ().strftime ('%Y%m%d')}.xlsx"
    return send_file (output ,download_name =nombre ,as_attachment =True )


    # ================= EXPORTACIÓN GHG PROTOCOL =================
@app .route ("/exportar_ghg")
def exportar_ghg ():
    if 'user_id'not in session :return redirect ("/")
    empresa =session .get ('empresa')
    anio =request.args.get('anio')
    incluir_huella_hidrica = request.args.get('incluir_huella_hidrica') in ('1', 'true', 'on', 'si')
    if anio is not None and anio != '' and anio != 'Todos' and (not re.fullmatch(r'\d{4}',anio) or int(anio)<1):
        flash('Selecciona un año válido para exportar el reporte GHG.','error')
        return redirect(url_for('exportar'))

    conn =get_db ()
    if anio in (None, ''):
        anio = _anio_panel(conn.cursor())

    filtro =" AND SUBSTRING(fecha::text,1,4) = %s"if anio !='Todos'else ""
    p1 =(empresa ,anio )if anio !='Todos'else (empresa ,)

    _case_alcance ="""
        CASE
            WHEN fuente IN ('Combustión Fija','Combustible Móvil','Combustión Estacionaria','Refrigerantes','Fugas de Refrigerantes') THEN 'Alcance 1'
            WHEN fuente = 'Electricidad' THEN 'Alcance 2'
            WHEN fuente = 'Residuos' THEN 'Alcance 3'
            ELSE COALESCE(alcance,'')
        END
    """
    df_a1 =pd .read_sql_query (
    f"SELECT fecha,area,fuente,categoria,actividad,unidad,cantidad,factor,emision "
    f"FROM registros WHERE empresa=%s AND ({_case_alcance })='Alcance 1'{filtro } ORDER BY fecha",
    conn ,params =p1 )

    df_a2 =pd .read_sql_query (
    f"SELECT fecha,area,fuente,categoria,actividad,unidad,cantidad,factor,emision,"
    f"COALESCE(emision_ubicacion,0) as emision_ubicacion,origen_energia,tiene_irec,COALESCE(sistema,'') as sistema "
    f"FROM registros WHERE empresa=%s AND ({_case_alcance })='Alcance 2'{filtro } ORDER BY fecha",
    conn ,params =p1 )

    df_a3 =pd .read_sql_query (
    f"SELECT fecha,area,fuente,categoria,actividad,unidad,cantidad,factor,emision "
    f"FROM registros WHERE empresa=%s AND ({_case_alcance })='Alcance 3'{filtro } ORDER BY fecha",
    conn ,params =p1 )

    hojas_agua = _datos_huella_hidrica_exportacion(conn, empresa, anio) if incluir_huella_hidrica else None
    conn .close ()

    total_a1 =float (df_a1 ['emision'].sum ())if not df_a1 .empty else 0.0 
    total_a2_mercado =float (df_a2 ['emision'].sum ())if not df_a2 .empty else 0.0 
    total_a2_ubicacion =float (df_a2 ['emision_ubicacion'].sum ())if not df_a2 .empty else 0.0 
    total_a3 =float (df_a3 ['emision'].sum ())if not df_a3 .empty else 0.0 

    df_resumen =pd .DataFrame ([
    {"Alcance":"Alcance 1 — Emisiones directas","Total kg CO₂e":round (total_a1 ,4 )},
    {"Alcance":"Alcance 2 (mercado) — Electricidad comprada","Total kg CO₂e":round (total_a2_mercado ,4 )},
    {"Alcance":"Alcance 2 (ubicación) — Electricidad comprada","Total kg CO₂e":round (total_a2_ubicacion ,4 )},
    {"Alcance":"Alcance 3 — Residuos y otras indirectas","Total kg CO₂e":round (total_a3 ,4 )},
    {"Alcance":"TOTAL GHG","Total kg CO₂e":round (total_a1 +total_a2_mercado +total_a3 ,4 )},
    ])

    col_map ={
    'fecha':'Fecha','area':'Área / Sucursal','fuente':'Fuente',
    'categoria':'Categoría / Combustible','actividad':'Actividad / Uso',
    'unidad':'Unidad','cantidad':'Cantidad','factor':'Factor de emisión',
    'emision':'Emisión (kg CO₂e)','emision_ubicacion':'Emisión ubicación (kg CO₂e)',
    'origen_energia':'Origen Energía','tiene_irec':'IREC','sistema':'Sistema Eléctrico'
    }
    for df in [df_a1 ,df_a2 ,df_a3 ]:
        _etiquetar_unidad_factor(df)
        df .rename (columns ={k :v for k ,v in col_map .items ()if k in df .columns },inplace =True )

    output =io .BytesIO ()
    with pd .ExcelWriter (output ,engine ='openpyxl')as writer :
        df_resumen .to_excel (writer ,sheet_name ='Resumen GHG',index =False )
        if not df_a1 .empty :
            df_a1 .to_excel (writer ,sheet_name ='Alcance 1',index =False )
        if not df_a2 .empty :
            df_a2 .to_excel (writer ,sheet_name ='Alcance 2 Electricidad',index =False )
        if not df_a3 .empty :
            df_a3 .to_excel (writer ,sheet_name ='Alcance 3',index =False )
        if hojas_agua:
            hojas_agua[0].to_excel(writer, sheet_name='Huella Hídrica', index=False)
            if not hojas_agua[1].empty:
                hojas_agua[1].to_excel(writer, sheet_name='Huella Hídrica Mensual', index=False)
            if not hojas_agua[2].empty:
                hojas_agua[2].to_excel(writer, sheet_name='Huella Hídrica Flujos', index=False)

        from openpyxl .styles import Font ,PatternFill ,Alignment 
        header_fill =PatternFill ("solid",fgColor ="064E3B")
        header_font =Font (bold =True ,color ="FFFFFF")
        total_fill =PatternFill ("solid",fgColor ="D1FAE5")
        total_font =Font (bold =True )

        for sheet_name ,ws in writer .sheets .items ():
            for cell in ws [1 ]:
                cell .fill =header_fill 
                cell .font =header_font 
                cell .alignment =Alignment (horizontal ='center')
            for col in ws .columns :
                ws .column_dimensions [col [0 ].column_letter ].width =22 
            if sheet_name =='Resumen GHG':
                last_row =ws .max_row 
                for cell in ws [last_row ]:
                    cell .fill =total_fill 
                    cell .font =total_font 

    output .seek (0 )
    nombre =f"Reporte_Oficial_{empresa .replace (' ','_')}_{anio }_{datetime .now ().strftime ('%Y%m%d')}.xlsx"
    return send_file (output ,download_name =nombre ,as_attachment =True )


    # ─── TICKETS ──────────────────────────────────────────────────────────────────

@app .route ('/tickets')
def tickets ():
    if not session .get ('user_id'):
        return redirect ('/')
    if session .get ('es_admin')==1 :
        return redirect ('/admin/tickets')
    empresa =session .get ('empresa')
    conn =get_db ()
    cur =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cur .execute ("SELECT * FROM tickets WHERE empresa = %s ORDER BY fecha_creacion DESC",(empresa ,))
    mis_tickets =cur .fetchall ()
    conn .close ()
    return render_template ('tickets.html',tickets =mis_tickets )


@app .route ('/tickets/nuevo',methods =['POST'])
def tickets_nuevo ():
    if not session .get ('user_id')or session .get ('es_admin')==1 :
        return redirect ('/')
    empresa =session .get ('empresa')
    asunto =request .form .get ('asunto','').strip ()
    descripcion =request .form .get ('descripcion','').strip ()
    prioridad =request .form .get ('prioridad','Normal')
    if not asunto or not descripcion :
        flash ('El asunto y la descripción son obligatorios.','error')
        return redirect ('/tickets')
    conn =get_db ()
    cur =conn .cursor ()
    cur .execute ("""
        INSERT INTO tickets (empresa, asunto, descripcion, prioridad, estado, fecha_creacion)
        VALUES (%s, %s, %s, %s, 'Abierto', %s)
    """,(empresa ,asunto ,descripcion ,prioridad ,datetime .now ().strftime ('%Y-%m-%d %H:%M')))
    conn .commit ()
    conn .close ()
    flash ('Ticket enviado correctamente. Te responderemos pronto.','success')
    return redirect ('/tickets')


@app .route ('/admin/tickets')
def admin_tickets ():
    if session .get ('es_admin')!=1 :
        return redirect ('/')
    filtro_estado =request .args .get ('estado','')
    filtro_empresa =request .args .get ('empresa','')
    conn =get_db ()
    cur =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    where =[]
    params =[]
    if filtro_estado :
        where .append ("estado = %s")
        params .append (filtro_estado )
    if filtro_empresa :
        where .append ("empresa ILIKE %s")
        params .append (f'%{filtro_empresa }%')
    sql ="SELECT * FROM tickets"
    if where :
        sql +=" WHERE "+" AND ".join (where )
    sql +=" ORDER BY fecha_creacion DESC"
    cur .execute (sql ,params )
    all_tickets =cur .fetchall ()
    cur .execute ("SELECT estado, COUNT(*) FROM tickets GROUP BY estado")
    conteos ={row [0 ]:row [1 ]for row in cur .fetchall ()}
    conn .close ()
    return render_template ('admin_tickets.html',tickets =all_tickets ,conteos =conteos ,
    filtro_estado =filtro_estado ,filtro_empresa =filtro_empresa )


@app .route ('/admin/tickets/<int:ticket_id>/responder',methods =['POST'])
def admin_tickets_responder (ticket_id ):
    if session .get ('es_admin')!=1 :
        return redirect ('/')
    respuesta =request .form .get ('respuesta','').strip ()
    nuevo_estado =request .form .get ('estado','Abierto')
    conn =get_db ()
    cur =conn .cursor ()
    cur .execute ("""
        UPDATE tickets SET respuesta = %s, estado = %s, fecha_respuesta = %s WHERE id = %s
    """,(respuesta ,nuevo_estado ,datetime .now ().strftime ('%Y-%m-%d %H:%M'),ticket_id ))
    conn .commit ()
    conn .close ()
    flash ('Respuesta enviada correctamente.','success')
    return redirect ('/admin/tickets')


    # Inicia la DB solo cuando se solicita explicitamente, para evitar bloquear el arranque en entornos con migraciones lentas.
if os .getenv ("GREENTRACK_INIT_DB","0")=="1":
    init_db ()

if __name__ =="__main__":
    app .run (host ="0.0.0.0",port =5000 ,debug =True )






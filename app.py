from flask import Flask ,render_template ,request ,redirect ,flash ,session ,send_file ,jsonify ,url_for 
import psycopg2 
import psycopg2 .extras 
from datetime import datetime 
import pandas as pd 
import hashlib 
import os 
import io 
import json 
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

app =Flask (__name__ )
app .secret_key =os .getenv ("SECRET_KEY","clave_segura_provisional")
app .config ["TEMPLATES_AUTO_RELOAD"]=True 
register_blueprints (app )

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

    # ConfiguraciÃ³n de Base de Datos para ProducciÃ³n (Railway) o Local
DATABASE_URL =os .getenv ("DATABASE_URL")

def get_db ():
# 1. Obtenemos la variable secreta de Railway
    db_url =os .getenv ("DATABASE_URL")

    # 2. Si Railway no estÃ¡ mandando la variable, esto evitarÃ¡ que el sistema colapse en silencio
    if not db_url :
        raise ValueError ("Â¡Error CRÃTICO: No se encontrÃ³ la variable DATABASE_URL en Railway!")

        # 3. CorrecciÃ³n automÃ¡tica: Railway a veces entrega la URL como "postgres://", 
        # pero Python exige que diga "postgresql://". Esto lo arregla solo.
    if db_url .startswith ("postgres://"):
        db_url =db_url .replace ("postgres://","postgresql://",1 )

        # 4. Conectamos forzando el modo seguro (SSL), que Railway a veces exige
    return psycopg2 .connect (db_url ,sslmode ='require')
    # Filtro para nÃºmeros con puntos y comas
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

        # Filtro compacto para KPIs: muestra K o M cuando el nÃºmero es grande
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

        # Filtro para mostrar Mes y AÃ±o
@app .template_filter ('mes_anio')
def mes_anio (fecha_str ):
    try :
        meses =["","Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
        fecha_obj =datetime .strptime (fecha_str [:10 ],"%Y-%m-%d")
        return f"{meses [fecha_obj .month ]} {fecha_obj .year }"
    except :
        return fecha_str 

        # Factores de emisiÃ³n base
FACTORES ={
"Electricidad":{"kWh":0.233 },
"Transporte":{"km":0.192 },
"Gas":{"m3":2.0 },
"Combustible mÃ³vil":{"L":2.68 },
"Residuos":{"kg":0.45 },
"Agua":{"m3":0.34 }
}

UNIDADES_PRODUCTIVAS =[
"Toneladas producto",
"Toneladas producidas",
"Kilogramos producidos",
"Personas",
"Horas de trabajo",
"% de ocupaciÃ³n",
"Noches ocupadas",
"Habitaciones vendidas",
"Pasajeros transportados",
"KilÃ³metros recorridos",
"Servicios prestados",
"Ã“rdenes procesadas",
"Visitas",
"Atenciones",
"M2 construidos",
"M2 operados",
"Litros embotellados",
"Toneladas transportadas",
"Tickets vendidos",
]

SECTORES_EMPRESA ={
"Turismo":["Transporte","Alojamiento","AlimentaciÃ³n","Agencias de viaje","Eventos","Empresas de transporte"],
"Servicios":["ConsultorÃ­a","TecnologÃ­a","Outsourcing","Backoffice","Call center","GestiÃ³n administrativa"],
"Salud":["ClÃ­nicas","Hospitales","Centros mÃ©dicos","Laboratorios","OdontologÃ­a","Telemedicina"],
"Comercio":["Retail","Mayoristas","E-commerce","DistribuciÃ³n","Importadora","Exportadora"],
"ConstrucciÃ³n":["Constructoras","Inmobiliarias","Obras civiles","Servicios de mantenimiento","Contratistas"],
"Industria y manufactura":["Alimentos y bebidas","MetalmecÃ¡nica","Textil","PlÃ¡sticos","QuÃ­mica","Celulosa y papel"],
"Agroindustria":["Fruticultura","LecherÃ­a","GanaderÃ­a","VitivinÃ­cola","Forestal","Procesamiento agrÃ­cola"],
"Transporte y logÃ­stica":["Carga terrestre","LogÃ­stica","Courier","Transporte marÃ­timo","Transporte aÃ©reo","Ãšltima milla"],
"EnergÃ­a y utilities":["GeneraciÃ³n elÃ©ctrica","DistribuciÃ³n elÃ©ctrica","Gas","Agua y saneamiento","EnergÃ­as renovables"],
"EducaciÃ³n":["Colegios","Universidades","Institutos","CapacitaciÃ³n","EducaciÃ³n tÃ©cnica"],
"Sector pÃºblico":["Municipalidades","Servicios pÃºblicos","Hospitales pÃºblicos","Universidades estatales","Gobierno regional"],
"MinerÃ­a":["ExtracciÃ³n","Servicios mineros","Proveedores mineros","ExploraciÃ³n","Procesamiento"],
"Finanzas":["Bancos","Aseguradoras","Fintech","Corredoras","Servicios financieros"],
}

def calcular_intensidad_emisiones (conn ,empresa ,anio ,valor_medida ):
    try :
        valor =float (valor_medida )
    except Exception :
        valor =0.0 
    if valor <=0 :
        return 0.0 ,None 
    cur =conn .cursor ()
    cur .execute (
    "SELECT COALESCE(SUM(emision), 0) FROM registros WHERE empresa = %s AND SUBSTRING(fecha::text, 1, 4) = %s",
    (empresa ,str (anio ))
    )
    total_kg =float (cur .fetchone ()[0 ]or 0 )
    total_t =round (total_kg /1000.0 ,4 )
    intensidad =round (total_kg /valor ,6 )
    return total_t ,intensidad 

def obtener_medidas_productivas (conn ,empresa ):
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("""
        SELECT anio, unidad, valor, fecha_actualizacion
        FROM medidas_productivas
        WHERE empresa = %s
        ORDER BY anio DESC, id DESC
    """,(empresa ,))
    medidas =[dict (row )for row in cursor .fetchall ()]
    for medida in medidas :
        total_t ,intensidad =calcular_intensidad_emisiones (conn ,empresa ,medida ['anio'],medida ['valor'])
        medida ['emisiones_anio_tco2e']=total_t 
        medida ['intensidad_emisiones']=intensidad 
    return medidas 

def obtener_medidas_empresas (conn ):
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("""
        SELECT empresa, anio, unidad, valor, fecha_actualizacion
        FROM medidas_productivas
        ORDER BY empresa, anio DESC, id DESC
    """)
    medidas ={}
    for row in cursor .fetchall ():
        emp =row ['empresa']
        if emp and emp not in medidas :
            medidas [emp ]=dict (row )
    for emp ,medida in medidas .items ():
        total_t ,intensidad =calcular_intensidad_emisiones (conn ,emp ,medida ['anio'],medida ['valor'])
        medida ['emisiones_anio_tco2e']=total_t 
        medida ['intensidad_emisiones']=intensidad 
    return medidas 

def guardar_medida_productiva (cursor ,empresa ,anio ,unidad ,valor ):
    cursor .execute ("""
        INSERT INTO medidas_productivas (empresa, anio, unidad, valor, fecha_actualizacion)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (empresa, anio) DO UPDATE SET
            unidad = EXCLUDED.unidad,
            valor = EXCLUDED.valor,
            fecha_actualizacion = EXCLUDED.fecha_actualizacion
    """,(empresa ,int (anio ),unidad ,float (valor ),datetime .now ().strftime ("%Y-%m-%d %H:%M")))

def _normalizar_texto_suministro (valor ):
    return (str (valor or '').strip ().lower ()
    .replace ('Ã©','e').replace ('é','e')
    .replace ('Ã³','o').replace ('ó','o')
    .replace ('Ã­','i').replace ('í','i')
    .replace ('Ã¡','a').replace ('á','a')
    .replace ('Ãº','u').replace ('ú','u'))

def _seed_factores_huella_suministro_agua (cursor ):
    fuente ="Report29-WaterFootprintBioenergy.pdf"
    referencia ="Gerbens-Leenes, Hoekstra & Van der Meer (2008), Value of Water Research Report Series No. 29, tablas 3 y 7"
    version ="March 2008"
    factores =[
    ("combustible","crude_oil_proxy",1.058),
    ("combustible","natural_gas",0.109),
    ("combustible","coal",0.164),
    ("electricidad","hydropower",22.300),
    ("electricidad","wind",0.000),
    ("electricidad","solar_thermal",0.265),
    ("electricidad","nuclear",0.086),
    ("electricidad","natural_gas",0.109),
    ("electricidad","coal",0.164),
    ]
    for tipo ,categoria ,factor in factores :
        cursor .execute ("""
            INSERT INTO factores_huella_suministro_agua
            (tipo_energia, categoria_factor, factor_m3_gj, fuente, referencia, version, activo)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (tipo_energia, categoria_factor, version)
            DO UPDATE SET factor_m3_gj = EXCLUDED.factor_m3_gj,
                          fuente = EXCLUDED.fuente,
                          referencia = EXCLUDED.referencia,
                          activo = TRUE
        """,(tipo ,categoria ,factor ,fuente ,referencia ,version ))

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
        if 'carbon'in texto or 'carbÃ³n'in texto :
            return 'electricidad','coal'
        return 'electricidad',None
    if any (x in texto for x in ['combustion','combusti','combustible']):
        if 'gas natural'in texto :
            return 'combustible','natural_gas'
        if 'carbon'in texto or 'carbÃ³n'in texto :
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
    if unidad_norm in ('m3','mÂ³','m³','metro cubico','metros cubicos'):
        if categoria_factor =='natural_gas'or 'gas natural'in categoria_norm :
            return cantidad_dec *Decimal ('0.0380')
    if unidad_norm in ('kg','kilogramo','kilogramos'):
        if categoria_factor =='coal':
            return cantidad_dec *Decimal ('0.0240')
        if categoria_factor =='natural_gas':
            return cantidad_dec *Decimal ('0.0500')
        return cantidad_dec *Decimal ('0.0430')
    return None 

def calcular_huella_suministro_registro (cursor ,registro_id ):
    cursor .execute ("""
        SELECT id, fecha, empresa, fuente, categoria, unidad, cantidad, origen_energia, sistema
        FROM registros
        WHERE id = %s
    """,(registro_id ,))
    registro =cursor .fetchone ()
    if not registro :
        return None 
    tipo_energia ,categoria_factor =_categoria_suministro_desde_registro (
    registro [3 ],registro [4 ],registro [7 ]if len (registro )>7 else None ,registro [8 ]if len (registro )>8 else None 
    )
    energia_gj =None 
    factor_id =None 
    factor_m3_gj =None 
    huella_m3 =None 
    calidad ="sin_factor"
    observaciones ="No hay factor de suministro hidrico aplicable para este consumo."
    if tipo_energia and categoria_factor :
        energia_gj =_energia_gj_desde_consumo (registro [6 ],registro [5 ],categoria_factor ,registro [4 ])
        if energia_gj is not None :
            cursor .execute ("""
                SELECT id, factor_m3_gj
                FROM factores_huella_suministro_agua
                WHERE tipo_energia = %s AND categoria_factor = %s AND activo = TRUE
                ORDER BY fecha_carga DESC, id DESC
                LIMIT 1
            """,(tipo_energia ,categoria_factor ))
            factor =cursor .fetchone ()
            if factor :
                factor_id =factor [0 ]
                factor_m3_gj =Decimal (str (factor [1 ]))
                huella_m3 =energia_gj *factor_m3_gj 
                calidad ="calculado_proxy"if categoria_factor =='crude_oil_proxy'else "calculado_bibliografico"
                observaciones ="Factor bibliografico de suministro energetico. No corresponde a factor local ni m3-eq."
        else :
            observaciones ="Unidad de consumo sin conversion energetica configurada para huella de suministro."
    cursor .execute ("""
        INSERT INTO resultados_huella_suministro_agua
        (registro_id, empresa, fecha, fuente_consumo, categoria_consumo, unidad_consumo,
         cantidad_consumo, energia_gj, factor_id, factor_m3_gj, huella_suministro_m3,
         calidad_resultado, observaciones, fecha_calculo)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (registro_id) DO UPDATE SET
            empresa = EXCLUDED.empresa,
            fecha = EXCLUDED.fecha,
            fuente_consumo = EXCLUDED.fuente_consumo,
            categoria_consumo = EXCLUDED.categoria_consumo,
            unidad_consumo = EXCLUDED.unidad_consumo,
            cantidad_consumo = EXCLUDED.cantidad_consumo,
            energia_gj = EXCLUDED.energia_gj,
            factor_id = EXCLUDED.factor_id,
            factor_m3_gj = EXCLUDED.factor_m3_gj,
            huella_suministro_m3 = EXCLUDED.huella_suministro_m3,
            calidad_resultado = EXCLUDED.calidad_resultado,
            observaciones = EXCLUDED.observaciones,
            fecha_calculo = NOW()
    """,(
    registro [0 ],registro [2 ],registro [1 ],registro [3 ],registro [4 ],registro [5 ],
    registro [6 ],energia_gj ,factor_id ,factor_m3_gj ,huella_m3 ,calidad ,observaciones 
    ))
    return {
    "registro_id":registro [0 ],
    "calidad":calidad ,
    "huella_suministro_m3":float (huella_m3 )if huella_m3 is not None else None ,
    }

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
    cursor .execute ("""
    CREATE TABLE IF NOT EXISTS medidas_productivas (
        id SERIAL PRIMARY KEY,
        empresa TEXT NOT NULL,
        anio INTEGER NOT NULL,
        unidad TEXT NOT NULL,
        valor REAL NOT NULL DEFAULT 0,
        fecha_actualizacion TEXT,
        UNIQUE (empresa, anio),
        FOREIGN KEY (empresa) REFERENCES usuarios(empresa)
    )
    """)
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
    cursor .execute ("CREATE TABLE IF NOT EXISTS combustible_movil (id SERIAL PRIMARY KEY, empresa TEXT, vehiculo_id INTEGER, periodo TEXT, combustible TEXT, cantidad REAL, unidad TEXT, costo REAL, fecha_registro TEXT, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
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
    # Migrar residuos DEFRA de unidad 'kg' a 'tonne' (factor es kg COâ‚‚e/t, no por kg)
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
        # Ãndice Ãºnico con tratamiento: permite un factor por (categoria, unidad, anio, tratamiento)
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
        WHERE fuente IN ('CombustiÃ³n Fija','Combustible MÃ³vil','CombustiÃ³n Estacionaria','Refrigerantes','Fugas de Refrigerantes')
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

    admin_password =hashlib .sha256 ("admin123".encode ()).hexdigest ()
    cursor .execute ("""
        INSERT INTO usuarios
        (empresa, email, password, contacto, es_admin, fecha_registro)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET password = EXCLUDED.password
    """,("Administrador","admin@huella.com",admin_password ,"Administrador",1 ,datetime .now ().strftime ("%Y-%m-%d %H:%M")))

    conn .commit ()
    conn .close ()

    # ================= DASHBOARD PRINCIPAL =================
@app .route ("/dashboard")
def dashboard ():
    if 'user_id'not in session or session .get ('es_admin')==1 :return redirect ("/")
    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )

    # 1. BUSCADOR DE AÃ‘OS
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
    params_reg =[empresa ]
    params_comb =[empresa ]
    filtro_fecha_reg =""
    filtro_fecha_comb =""

    # FILTRO: Usamos cm.fecha_registro para evitar ambigÃ¼edades en el JOIN
    if anio_seleccionado !='Todos':
        filtro_fecha_reg =" AND SUBSTRING(fecha::text, 1, 4) = %s"
        filtro_fecha_comb =" AND SUBSTRING(cm.fecha_registro::text, 1, 4) = %s"
        params_reg .append (anio_seleccionado )
        params_comb .append (anio_seleccionado )

        # 2. TOTALES Y VEHÃCULOS
    cursor .execute (f"SELECT SUM(emision) FROM registros WHERE empresa = %s {filtro_fecha_reg }",params_reg )
    res_reg =cursor .fetchone ()
    total_reg =float (res_reg [0 ])if res_reg and res_reg [0 ]else 0.0 
    total_comb =0.0 # incluido en registros desde ahora

    # Breakdown por vehÃ­culo (para la tarjeta de flota en el dashboard)
    cursor .execute (f"SELECT cm.cantidad, cm.combustible, v.patente, v.tipo FROM combustible_movil cm LEFT JOIN vehiculos v ON cm.vehiculo_id = v.id WHERE cm.empresa = %s {filtro_fecha_comb }",params_comb )
    comb_data =cursor .fetchall ()
    vehiculos_dict ={}
    factores_v ={"diesel":2.68 ,"bencina":2.31 ,"glp":1.61 ,"gas_natural":2.02 ,"electricidad":0.233 }
    for row in comb_data :
        f =factores_v .get (str (row ['combustible']).lower (),2.5 )
        emision =(float (row ['cantidad'])or 0.0 )*f 
        pat =row ['patente']or 'Sin Asignar'
        if pat not in vehiculos_dict :
            vehiculos_dict [pat ]={'patente':pat ,'tipo':row ['tipo']or 'N/A','registros':0 ,'total_emision':0.0 }
        vehiculos_dict [pat ]['registros']+=1 
        vehiculos_dict [pat ]['total_emision']+=emision 

    cursor .execute ("SELECT * FROM vehiculos WHERE empresa = %s",(empresa ,))
    vehiculos =cursor .fetchall ()

    # 3. GRÃFICO (ConversiÃ³n a Float para no romper el JSON)
    cursor .execute (f"SELECT fuente, SUM(emision) as total FROM registros WHERE empresa = %s {filtro_fecha_reg } GROUP BY fuente",params_reg )
    categorias =[{'fuente':row ['fuente'],'total':float (row ['total'])if row ['total']else 0.0 }for row in cursor .fetchall ()]

    # 4. CONTEO TOTAL Y ÃšLTIMOS REGISTROS
    cursor .execute (f"SELECT COUNT(*) FROM registros WHERE empresa = %s {filtro_fecha_reg }",params_reg )
    total_registros =cursor .fetchone ()[0 ]or 0 

    cursor .execute (f"""
        SELECT id, fecha, fuente, actividad, cantidad, unidad, emision, 'manual' as tipo_tabla
        FROM registros WHERE empresa = %s {filtro_fecha_reg }
        ORDER BY fecha DESC LIMIT 5
    """,params_reg )
    ultimos =[dict (row )for row in cursor .fetchall ()]

    # 5. TENDENCIA MENSUAL POR ALCANCE
    # Incluye registros manuales y combustible móvil, que vive en otra tabla.
    params_tendencia =[empresa ]
    if anio_seleccionado !='Todos':
        params_tendencia .append (anio_seleccionado )
        params_tendencia .append (empresa )
        params_tendencia .append (anio_seleccionado )
    else :
        params_tendencia .append (empresa )

    cursor .execute (f"""
        SELECT mes, alcance, SUM(total) as total
        FROM (
            SELECT
                SUBSTRING(fecha::text, 1, 7) as mes,
                CASE
                    WHEN fuente IN (
                        'CombustiÃ³n Estacionaria', 'CombustiÃ³n Fija', 'Combustible MÃ³vil', 'CombustiÃ³n MÃ³vil',
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

            UNION ALL

            SELECT
                SUBSTRING(cm.fecha_registro::text, 1, 7) as mes,
                1 as alcance,
                COALESCE(cm.cantidad, 0) * CASE LOWER(COALESCE(cm.combustible, ''))
                    WHEN 'diesel' THEN 2.68
                    WHEN 'bencina' THEN 2.31
                    WHEN 'glp' THEN 1.61
                    WHEN 'gas_natural' THEN 2.02
                    WHEN 'electricidad' THEN 0.233
                    ELSE 2.5
                END as total
            FROM combustible_movil cm
            WHERE cm.empresa = %s {filtro_fecha_comb }
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

    alcance_a1 =round (sum (tendencia_a1 ),2 )
    alcance_a2 =round (sum (tendencia_a2 ),2 )
    alcance_a3 =round (sum (tendencia_a3 ),2 )
    alcances_data =[
    {'nombre':'Alcance 1 - Directo','total':alcance_a1 ,'color':'#EF4444'},
    {'nombre':'Alcance 2 - Electricidad','total':alcance_a2 ,'color':'#F59E0B'},
    {'nombre':'Alcance 3 - Residuos','total':alcance_a3 ,'color':'#10B981'},
    ]

    cursor .execute ("""
        SELECT anio, unidad, valor, fecha_actualizacion
        FROM medidas_productivas
        WHERE empresa = %s
        ORDER BY anio DESC, id DESC
    """,(empresa ,))
    medidas_productivas =[dict (row )for row in cursor .fetchall ()]
    intensidad_anio =None 
    intensidad_unidad =None 
    intensidad_emisiones =0.0 
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

    agua_params =[empresa ]
    agua_where =""
    suministro_params =[empresa ]
    suministro_where =""
    if anio_seleccionado !='Todos':
        agua_where =" AND SUBSTRING(periodo::text, 1, 4) = %s"
        suministro_where =" AND SUBSTRING(fecha::text, 1, 4) = %s"
        agua_params .append (anio_seleccionado )
        suministro_params .append (anio_seleccionado )

    cursor .execute (f"""
        SELECT
            COALESCE(SUM(CASE WHEN tipo_flujo = 'captacion' THEN volumen_m3 ELSE 0 END), 0) AS captacion,
            COALESCE(SUM(CASE WHEN tipo_flujo = 'retorno' AND retorna_mismo_sistema_hidrico = TRUE THEN volumen_m3 ELSE 0 END), 0) AS retorno_mismo
        FROM agua_flujos
        WHERE empresa = %s {agua_where}
    """,agua_params )
    agua_row =cursor .fetchone ()
    huella_azul_directa =max (0.0 ,float ((agua_row ['captacion']or 0 )-(agua_row ['retorno_mismo']or 0 )))if agua_row else 0.0
    huella_verde_directa =0.0

    cursor .execute (f"""
        SELECT COALESCE(SUM(huella_suministro_m3), 0) AS total
        FROM resultados_huella_suministro_agua
        WHERE empresa = %s {suministro_where}
          AND huella_suministro_m3 IS NOT NULL
    """,suministro_params )
    suministro_row =cursor .fetchone ()
    huella_suministro_energetico =float (suministro_row ['total']or 0 )if suministro_row else 0.0
    huella_hidrica_total =huella_azul_directa +huella_verde_directa +huella_suministro_energetico
    intensidad_hidrica_total =None 
    if intensidad_medida >0 :
        intensidad_total_decimal =calcular_intensidad_hidrica_total (huella_hidrica_total ,intensidad_medida )
        intensidad_hidrica_total =round (float (intensidad_total_decimal ),6 )if intensidad_total_decimal is not None else None 
    huella_hidrica_tipos =[
    {"tipo":"Huella azul directa","valor":round (huella_azul_directa ,6 ),"color":"#2487F3"},
    {"tipo":"Huella verde directa","valor":round (huella_verde_directa ,6 ),"color":"#22C55E"},
    {"tipo":"Suministro energetico","valor":round (huella_suministro_energetico ,6 ),"color":"#9B5CF6"},
    ]

            # 6. VARIACIÃ“N INTERANUAL
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
    intensidad_anio =intensidad_anio )

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

    # Opciones para los filtros desplegables
    alcances_disponibles =['Alcance 1','Alcance 2','Alcance 3']
    cursor .execute ("SELECT DISTINCT fuente FROM registros WHERE empresa = %s AND fuente IS NOT NULL ORDER BY fuente",(empresa ,))
    fuentes_disponibles =[r [0 ]for r in cursor .fetchall ()]
    cursor .execute ("SELECT DISTINCT SUBSTRING(fecha::text,1,4) as anio FROM registros WHERE empresa = %s AND fecha IS NOT NULL ORDER BY anio DESC",(empresa ,))
    anios_disponibles =[r [0 ]for r in cursor .fetchall ()]

    _fuentes_a1 =('CombustiÃ³n Fija','Combustible MÃ³vil','CombustiÃ³n Estacionaria','Refrigerantes','Fugas de Refrigerantes')
    _fuentes_a2 =('Electricidad',)
    _fuentes_a3 =('Residuos',)

    # WHERE dinÃ¡mico
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
@app .route ("/combustion")
def combustion_dashboard ():
    if 'user_id'not in session :
        return redirect ("/")

    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )

    fuentes_comb =(
    'CombustiÃ³n Estacionaria','CombustiÃ³n Fija','CombustiÃ³n MÃ³vil','Combustible MÃ³vil',
    'Combustión Estacionaria','Combustión Fija','Combustión Móvil','Combustible Móvil'
    )
    fuentes_fijas =('CombustiÃ³n Estacionaria','CombustiÃ³n Fija','Combustión Estacionaria','Combustión Fija')
    fuentes_moviles =('CombustiÃ³n MÃ³vil','Combustible MÃ³vil','Combustión Móvil','Combustible Móvil')

    cursor .execute ("SELECT fecha, fuente, categoria, actividad, cantidad, unidad, emision FROM registros WHERE empresa = %s AND fuente IN %s ORDER BY fecha DESC LIMIT 10",(empresa ,fuentes_comb ))
    registros_comb =[dict (row )for row in cursor .fetchall ()]

    cursor .execute ("SELECT categoria, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente IN %s GROUP BY categoria",(empresa ,fuentes_comb ))
    grafico_data =[dict (row )for row in cursor .fetchall ()]

    cursor .execute ("SELECT SUM(emision) FROM registros WHERE empresa = %s AND fuente IN %s",(empresa ,fuentes_comb ))
    res =cursor .fetchone ()
    total_emision =res [0 ]if res and res [0 ]else 0 

    cursor .execute ("""
        SELECT SUBSTRING(fecha::text, 1, 7) as mes, fuente, SUM(emision) as total
        FROM registros WHERE empresa = %s AND fuente IN %s
        GROUP BY 1, 2 ORDER BY 1
    """,(empresa ,fuentes_comb ))
    tendencia_comb_raw =cursor .fetchall ()
    meses_comb =sorted ({row ['mes']for row in tendencia_comb_raw })
    tendencia_fija =[]
    tendencia_movil =[]
    for m in meses_comb :
        fija =sum (float (r ['total']or 0 )for r in tendencia_comb_raw if r ['mes']==m and r ['fuente'] in fuentes_fijas)
        movil =sum (float (r ['total']or 0 )for r in tendencia_comb_raw if r ['mes']==m and r ['fuente'] in fuentes_moviles)
        tendencia_fija .append (round (fija ,2 ))
        tendencia_movil .append (round (movil ,2 ))

    conn .close ()
    return render_template ("combustion_dashboard.html",registros =registros_comb ,grafico_data =grafico_data ,
    total_emision =total_emision ,meses_comb =meses_comb ,
    tendencia_fija =tendencia_fija ,tendencia_movil =tendencia_movil )

@app .route ("/electricidad")
def electricidad_dashboard ():
    if 'user_id'not in session :return redirect ("/")

    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )

    cursor .execute (
    "SELECT fecha, categoria, actividad, cantidad, unidad, emision, emision_ubicacion, "
    "origen_energia, tiene_irec, COALESCE(sistema, '') as sistema, factor "
    "FROM registros WHERE empresa = %s AND fuente = 'Electricidad' ORDER BY fecha DESC LIMIT 20",
    (empresa ,)
    )
    rows =cursor .fetchall ()
    registros_elec =[]
    for r in rows :
        d =dict (r )
        cant =d ['cantidad']or 0 
        d ['factor_ubicacion']=round (d ['emision_ubicacion']/cant ,6 )if cant else 0.0 
        registros_elec .append (d )
    cursor .execute (
    "SELECT sistema, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad' GROUP BY sistema",
    (empresa ,)
    )
    grafico_data_raw =cursor .fetchall ()
    if not grafico_data_raw :
        cursor .execute (
        "SELECT categoria, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad' GROUP BY categoria",
        (empresa ,)
        )
        grafico_data_raw =cursor .fetchall ()
    grafico_data =[dict (r )for r in grafico_data_raw ]

    cursor .execute ("SELECT SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad'",(empresa ,))
    res =cursor .fetchone ()
    total_mercado =float (res ['total'])if res and res ['total']else 0.0 

    cursor .execute ("SELECT SUM(emision_ubicacion) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad'",(empresa ,))
    res2 =cursor .fetchone ()
    total_ubicacion =float (res2 ['total'])if res2 and res2 ['total']else 0.0 

    cursor .execute ("SELECT SUM(cantidad) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad'",(empresa ,))
    res3 =cursor .fetchone ()
    total_kwh =float (res3 ['total'])if res3 and res3 ['total']else 0.0 

    cursor .execute ("SELECT COUNT(*) as total FROM registros WHERE empresa = %s AND fuente = 'Electricidad' AND tiene_irec = 'Si'",(empresa ,))
    total_irec =cursor .fetchone ()['total']or 0 

    cursor .execute ("""
        SELECT SUBSTRING(fecha::text, 1, 7) as mes,
               SUM(cantidad) as kwh,
               SUM(emision) as emision_mercado,
               SUM(emision_ubicacion) as emision_ubicacion
        FROM registros WHERE empresa = %s AND fuente = 'Electricidad'
        GROUP BY 1 ORDER BY 1
    """,(empresa ,))
    tendencia_elec =[dict (r )for r in cursor .fetchall ()]

    conn .close ()
    return render_template (
    "electricidad_dashboard.html",
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


def _agua_calcular_resumen (conn ,empresa ,periodo =None ):
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    params =[empresa ]
    where_periodo =""
    if periodo :
        where_periodo =" AND to_char(periodo, 'YYYY-MM') = %s"
        params .append (periodo )

    cursor .execute (
    f"""
        SELECT sede_id, periodo, tipo_flujo, fuente_agua, origen_hidrico, destino_agua, volumen_m3,
               retorna_mismo_sistema_hidrico, tiene_tratamiento, calidad_dato, evidencia, observaciones
        FROM agua_flujos
        WHERE empresa = %s{where_periodo }
        ORDER BY periodo DESC, id DESC
        """,
    tuple (params ),
    )
    flujos =[dict (r )for r in cursor .fetchall ()]

    cursor .execute ("SELECT * FROM agua_sedes WHERE empresa = %s ORDER BY nombre_sede",(empresa ,))
    sedes =[dict (r )for r in cursor .fetchall ()]
    sede_map ={s ["id"]:s for s in sedes }

    cursor .execute (
    """
        SELECT * FROM factores_escasez_agua
        WHERE activo = TRUE
        ORDER BY fecha_carga DESC
        """
    )
    factores =[dict (r )for r in cursor .fetchall ()]

    periodo_elegido =periodo or (_agua_periodo_mes (flujos [0 ].get ("periodo"))if flujos else None )
    grupos_flujos ={}
    for flujo in flujos :
        grupos_flujos .setdefault (flujo .get ("sede_id"),[]).append (flujo )

    resultados_sede =[]
    for sede_id ,grupo in grupos_flujos .items ():
        sede =sede_map .get (sede_id ,{"id":sede_id ,"empresa":empresa ,"nombre_sede":"Empresa"if sede_id is None else f"Sede {sede_id }","region":None ,"comuna":None ,"codigo_cuenca":None ,"nombre_cuenca":None ,"pais":"Chile"})
        resultados_sede .append ({
        "sede_id":sede_id ,
        "sede":sede ,
        "resultado":consolidar_resultado_sede (grupo ,sede ,factores ,periodo_elegido ,medida_productiva =None ),
        })

    captacion_total =sum (Decimal (str (r ["resultado"]["captacion_m3"]or 0 ))for r in resultados_sede )
    retornos_totales =sum (Decimal (str (r ["resultado"]["retorno_m3"]or 0 ))for r in resultados_sede )
    retorno_mismo =sum (Decimal (str (r ["resultado"]["retorno_mismo_sistema_m3"]or 0 ))for r in resultados_sede )
    reuso =sum (Decimal (str (r ["resultado"]["reuso_m3"]or 0 ))for r in resultados_sede )
    consumo =sum (Decimal (str (r ["resultado"]["consumo_operativo_m3"]or 0 ))for r in resultados_sede )
    calidad =generar_indicador_calidad_datos (flujos )if flujos else "baja"
    valor_intensidad =None 
    valor_intensidad_esc =None 
    huella =None 
    factor =None 
    nivel ="Datos insuficientes"

    if flujos and resultados_sede :
        grupos_con_datos =[r for r in resultados_sede if any (
        Decimal (str (r ["resultado"][campo ]or 0 ))>0 
        for campo in ("captacion_m3","retorno_m3","reuso_m3","consumo_operativo_m3")
        )]
        if grupos_con_datos and all (r ["resultado"]["factor"]is not None for r in grupos_con_datos ):
            huella =sum (
            Decimal (str (r ["resultado"]["huella_escasez_m3eq"]or 0 ))
            for r in grupos_con_datos 
            if r ["resultado"]["huella_escasez_m3eq"]is not None 
            )
            if len (grupos_con_datos )==1 :
                factor =grupos_con_datos [0 ]["resultado"]["factor"]
            nivel ="Huella de escasez calculada"if consumo >0 else "Solo inventario físico disponible"
        else :
            nivel ="Solo inventario físico disponible"

    cursor .execute ("SELECT COUNT(*) FROM agua_sedes WHERE empresa = %s",(empresa ,))
    total_sedes =cursor .fetchone ()[0 ]or 0 
    cursor .execute ("SELECT COUNT(DISTINCT sede_id) FROM agua_flujos WHERE empresa = %s AND sede_id IS NOT NULL",(empresa ,))
    sedes_con_datos =cursor .fetchone ()[0 ]or 0 
    cursor .execute ("SELECT COUNT(DISTINCT to_char(periodo, 'YYYY-MM')) FROM agua_flujos WHERE empresa = %s",(empresa ,))
    periodos_con_datos =cursor .fetchone ()[0 ]or 0 
    cursor .execute (
    """
        SELECT COUNT(*)
        FROM (
            SELECT to_char(periodo, 'YYYY-MM') AS periodo,
                   SUM(CASE WHEN tipo_flujo = 'captacion' THEN volumen_m3 ELSE 0 END) AS captacion,
                   SUM(CASE WHEN tipo_flujo = 'retorno' THEN volumen_m3 ELSE 0 END) AS retorno,
                   SUM(CASE WHEN tipo_flujo = 'reuso' THEN volumen_m3 ELSE 0 END) AS reuso
            FROM agua_flujos
            WHERE empresa = %s
            GROUP BY 1
        ) meses
        WHERE captacion > 0 AND retorno > 0
        """,
    (empresa ,),
    )
    periodos_inventario_completo =cursor .fetchone ()[0 ]or 0 

    cursor .execute ("SELECT SUM(valor) FROM medidas_productivas WHERE empresa = %s",(empresa ,))
    # No se usa directamente: evitamos confundir intensidad por suma anual.

    # Intensidad hÃ­drica usando la medida productiva del perÃ­odo visible si existe
    intensidad_hidrica =None 
    intensidad_escasez =None 
    intensidad_hidrica_total =None 
    medida_actual =None 
    if periodo :
        try :
            anio_periodo =int (str (periodo )[:4 ])
            cursor .execute (
            "SELECT anio, unidad, valor FROM medidas_productivas WHERE empresa = %s AND anio = %s ORDER BY id DESC LIMIT 1",
            (empresa ,anio_periodo ),
            )
            medida_actual =cursor .fetchone ()
        except Exception :
            medida_actual =None 
    else :
        cursor .execute (
        "SELECT anio, unidad, valor FROM medidas_productivas WHERE empresa = %s ORDER BY anio DESC, id DESC LIMIT 1",
        (empresa ,),
        )
        medida_actual =cursor .fetchone ()

    medida_valor =0 
    if medida_actual and medida_actual [2 ]:
        try :
            medida_valor =float (medida_actual [2 ])
        except Exception :
            medida_valor =0 
    if medida_valor >0 :
        intensidad_hidrica =round (float (consumo )/medida_valor ,6 )
        intensidad_total_decimal =calcular_intensidad_hidrica_total (huella_hidrica_total ,medida_valor )
        intensidad_hidrica_total =round (float (intensidad_total_decimal ),6 )if intensidad_total_decimal is not None else None 
        if huella is not None :
            intensidad_escasez =round (float (huella )/medida_valor ,6 )

    tendencia ={}
    cursor .execute (
    """
        SELECT to_char(periodo, 'YYYY-MM') AS mes,
               SUM(CASE WHEN tipo_flujo = 'captacion' THEN volumen_m3 ELSE 0 END) AS captacion,
               SUM(CASE WHEN tipo_flujo = 'retorno' AND retorna_mismo_sistema_hidrico = TRUE THEN volumen_m3 ELSE 0 END) AS retorno,
               SUM(CASE WHEN tipo_flujo = 'reuso' THEN volumen_m3 ELSE 0 END) AS reuso
        FROM agua_flujos
        WHERE empresa = %s
        GROUP BY 1
        ORDER BY 1
        """,
    (empresa ,),
    )
    for row in cursor .fetchall ():
        tendencia [row [0 ]]={
        "captacion":float (row [1 ]or 0 ),
        "retorno":float (row [2 ]or 0 ),
        "reuso":float (row [3 ]or 0 ),
        }

    por_fuente ={}
    cursor .execute (
    """
        SELECT COALESCE(fuente_agua, 'Sin fuente') AS fuente, SUM(volumen_m3) AS total
        FROM agua_flujos
        WHERE empresa = %s AND tipo_flujo = 'captacion'
        GROUP BY 1
        ORDER BY total DESC
        """,
    (empresa ,),
    )
    for row in cursor .fetchall ():
        por_fuente [row [0 ]]=float (row [1 ]or 0 )

    por_sede ={}
    cursor .execute (
    """
        SELECT sede_id, SUM(CASE WHEN tipo_flujo = 'captacion' THEN volumen_m3 ELSE 0 END) AS captacion,
               SUM(CASE WHEN tipo_flujo = 'retorno' AND retorna_mismo_sistema_hidrico = TRUE THEN volumen_m3 ELSE 0 END) AS retorno,
               SUM(CASE WHEN tipo_flujo = 'reuso' THEN volumen_m3 ELSE 0 END) AS reuso
        FROM agua_flujos
        WHERE empresa = %s
        GROUP BY sede_id
        ORDER BY captacion DESC
        """,
    (empresa ,),
    )
    for row in cursor .fetchall ():
        sede =sede_map .get (row [0 ],{})
        nombre_sede =sede .get ("nombre_sede")if sede else "Empresa"
        por_sede [nombre_sede if row [0 ]is not None else "Empresa"]={
        "captacion":float (row [1 ]or 0 ),
        "retorno":float (row [2 ]or 0 ),
        "reuso":float (row [3 ]or 0 ),
        }

    suministro_params =[empresa ]
    suministro_where =""
    if periodo :
        suministro_where =" AND SUBSTRING(fecha::text, 1, 7) = %s"
        suministro_params .append (periodo )
    cursor .execute (
    f"""
        SELECT COALESCE(SUM(huella_suministro_m3), 0) AS total
        FROM resultados_huella_suministro_agua
        WHERE empresa = %s{suministro_where}
          AND huella_suministro_m3 IS NOT NULL
        """,
    tuple (suministro_params ),
    )
    huella_suministro_total =cursor .fetchone ()[0 ]or 0 

    cursor .execute (
    f"""
        SELECT COALESCE(fuente_consumo, 'Sin fuente') AS fuente,
               COALESCE(categoria_consumo, 'Sin categoria') AS categoria,
               SUM(huella_suministro_m3) AS total
        FROM resultados_huella_suministro_agua
        WHERE empresa = %s{suministro_where}
          AND huella_suministro_m3 IS NOT NULL
        GROUP BY 1, 2
        ORDER BY total DESC
        """,
    tuple (suministro_params ),
    )
    suministro_por_fuente ={f"{row [0 ]} - {row [1 ]}":float (row [2 ]or 0 )for row in cursor .fetchall ()}

        # Cobertura por sede para el dashboard
    cobertura =[]
    if sedes :
        sedes_iter =sedes 
    else :
        sedes_iter =[{"id":None ,"nombre_sede":"Empresa","empresa":empresa ,"region":None ,"comuna":None ,"latitud":None ,"longitud":None ,"codigo_cuenca":None ,"nombre_cuenca":None }]
    for sede in sedes_iter :
        cursor .execute (
        """
            SELECT to_char(periodo, 'YYYY-MM') AS periodo,
                   SUM(CASE WHEN tipo_flujo = 'captacion' THEN volumen_m3 ELSE 0 END) AS captacion,
                   SUM(CASE WHEN tipo_flujo = 'retorno' THEN volumen_m3 ELSE 0 END) AS retorno,
                   SUM(CASE WHEN tipo_flujo = 'reuso' THEN volumen_m3 ELSE 0 END) AS reuso
            FROM agua_flujos
            WHERE empresa = %s AND sede_id IS NOT DISTINCT FROM %s
            GROUP BY 1
            ORDER BY 1 DESC
            LIMIT 1
            """,
        (empresa ,sede ["id"]),
        )
        row =cursor .fetchone ()
        has_data =bool (row and any (float (v or 0 )>0 for v in row [1 :]))
        factor_sede =buscar_factor_escasez_mas_especifico (factores ,sede ,periodo or (row [0 ]if row else None ))
        huella_sede =None 
        if row and factor_sede :
            try :
                capt =Decimal (str (float (row [1 ]or 0 )))
                ret =Decimal (str (float (row [2 ]or 0 )))
                cons =calcular_consumo_operativo_estimado (capt ,ret ,Decimal (str (float (row [3 ]or 0 ))))
                huella_sede =calcular_huella_escasez (cons ,factor_sede )
            except Exception :
                huella_sede =None 
        cobertura .append ({
        "sede":sede ,
        "periodo":row [0 ]if row else None ,
        "captacion":float (row [1 ]or 0 )if row else 0.0 ,
        "retorno":float (row [2 ]or 0 )if row else 0.0 ,
        "reuso":float (row [3 ]or 0 )if row else 0.0 ,
        "ubicacion":"Completa"if sede .get ("codigo_cuenca")and sede .get ("nombre_cuenca")and sede .get ("region")and sede .get ("comuna")else "Parcial"if sede .get ("region")or sede .get ("comuna")else "Pendiente",
        "factor_disponible":bool (factor_sede ),
        "resultado":"Huella de escasez calculada"if factor_sede and has_data else ("Solo inventario fÃ­sico disponible"if has_data else "Datos insuficientes"),
        "huella_escasez":float (huella_sede )if huella_sede is not None else None ,
        })

    return {
    "sedes":sedes ,
    "flujos":flujos ,
    "captacion_total":float (captacion_total or 0 ),
    "retornos_totales":float (retornos_totales or 0 ),
    "retorno_mismo":float (retorno_mismo or 0 ),
    "reuso":float (reuso or 0 ),
    "consumo":float (consumo or 0 ),
    "factor":factor ,
    "huella":float (huella or 0 )if huella is not None else None ,
    "nivel":nivel ,
    "calidad":calidad ,
    "intensidad_hidrica":intensidad_hidrica ,
    "intensidad_escasez":intensidad_escasez ,
    "intensidad_hidrica_total":intensidad_hidrica_total ,
    "tendencia":tendencia ,
    "por_fuente":por_fuente ,
    "por_sede":por_sede ,
    "huella_suministro_total":float (huella_suministro_total or 0 ),
    "suministro_por_fuente":suministro_por_fuente ,
    "cobertura":cobertura ,
    "factores":factores ,
    "total_sedes":total_sedes ,
    "sedes_con_datos":sedes_con_datos ,
    "periodos_con_datos":periodos_con_datos ,
    "periodos_inventario_completo":periodos_inventario_completo ,
    }


def _agua_agrupar_resultados_reportes (flujos ,sedes ,factores ,medida_valor =None ,vista ="mensual"):
    sede_map ={s ["id"]:s for s in sedes }
    grupos ={}
    for flujo in flujos :
        periodo_flujo =str (flujo ["periodo"])[:7 ]if vista =="mensual"else str (flujo ["periodo"])[:4 ]
        grupos .setdefault ((flujo ["sede_id"],periodo_flujo ),[]).append (flujo )

    resultados =[]
    for (sede_id ,periodo_sel ),grupo in grupos .items ():
        sede =sede_map .get (sede_id ,{"id":sede_id ,"nombre_sede":"Empresa"if sede_id is None else f"Sede {sede_id }"})
        resultado =consolidar_resultado_sede (grupo ,sede ,factores ,periodo_sel ,medida_valor )
        resultados .append ({
        "sede_id":sede_id ,
        "nombre_sede":sede .get ("nombre_sede"),
        "periodo":periodo_sel ,
        "factor_metodo":resultado ["factor"].metodo if resultado .get ("factor")else None ,
        "factor_version":resultado ["factor"].version_metodo if resultado .get ("factor")else None ,
        "factor_codigo":resultado ["factor"].codigo_geografico if resultado .get ("factor")else None ,
        "factor_fuente":resultado ["factor"].fuente if resultado .get ("factor")else None ,
        "factor_vigencia_inicio":resultado ["factor"].periodo_inicio if resultado .get ("factor")else None ,
        "factor_vigencia_fin":resultado ["factor"].periodo_fin if resultado .get ("factor")else None ,
        **{k :(float (v )if isinstance (v ,Decimal )else v )for k ,v in resultado .items ()if k !="factor"},
        })
    return resultados 


@app .route ("/agua")
def agua_dashboard ():
    if 'user_id'not in session :
        return redirect ("/")
    empresa =session .get ('empresa')
    periodo =request .args .get ("periodo")
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute (
    """
        SELECT DISTINCT to_char(periodo, 'YYYY-MM') AS periodo
        FROM agua_flujos
        WHERE empresa = %s
        ORDER BY periodo DESC
    """,
    (empresa ,),
    )
    periodos_disponibles =[r [0 ]for r in cursor .fetchall ()]
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

    fuentes_ref =('Refrigerantes','Fugas de Refrigerantes')
    cursor .execute ("SELECT SUM(emision) as total FROM registros WHERE empresa = %s AND fuente IN %s",(empresa ,fuentes_ref ))
    res =cursor .fetchone ()
    total_refrigerantes =res ['total']if res and res ['total']else 0.0 

    cursor .execute ("SELECT categoria, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente IN %s GROUP BY categoria ORDER BY total DESC",(empresa ,fuentes_ref ))
    datos_gases =[dict (row )for row in cursor .fetchall ()]

    cursor .execute ("SELECT SUBSTRING(fecha, 1, 7) as mes, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente IN %s GROUP BY mes ORDER BY mes",(empresa ,fuentes_ref ))
    datos_meses =[dict (row )for row in cursor .fetchall ()]

    cursor .execute ("SELECT fecha, categoria, cantidad, unidad, factor, emision FROM registros WHERE empresa = %s AND fuente IN %s ORDER BY fecha DESC LIMIT 10",(empresa ,fuentes_ref ))
    rows =cursor .fetchall ()

    # Cargar factores de la tabla factores para recuperar emisiones de registros con factor=0
    cursor .execute ("SELECT categoria, unidad, factor FROM factores WHERE factor > 0")
    factores_lookup ={}
    for frow in cursor .fetchall ():
        factores_lookup [(frow ['categoria'],frow ['unidad'])]=float (frow ['factor'])

    ultimos_registros =[]
    for row in rows :
        r =dict (row )
        cant =r .get ('cantidad')or 0 
        em =r .get ('emision')or 0 
        fac =r .get ('factor')or 0 

        if cant ==0 and em >0 and fac >0 :
        # Caso 1: cantidad perdida, recuperar desde emision/factor
            r ['cantidad']=round (em /fac ,6 )
        elif em ==0 and cant >0 :
        # Caso 2: emision guardada como 0 (factor era 0 al guardar), buscar factor real
            fac_real =(factores_lookup .get ((r ['categoria'],r ['unidad']))or 
            factores_lookup .get ((r ['categoria'],'kg')))
            if fac_real :
                r ['emision']=round (cant *fac_real ,4 )
                r ['factor']=fac_real 
        ultimos_registros .append (r )

    cursor .execute ("SELECT COUNT(DISTINCT categoria) as total FROM registros WHERE empresa = %s AND fuente IN %s",(empresa ,fuentes_ref ))
    res_gases =cursor .fetchone ()
    total_gases_distintos =res_gases ['total']if res_gases and res_gases ['total']else 0 

    gas_top =datos_gases [0 ]if datos_gases else None 

    conn .close ()
    return render_template ("refrigerantes_dashboard.html",total_refrigerantes =total_refrigerantes ,
    datos_gases =datos_gases ,datos_meses =datos_meses ,registros =ultimos_registros ,
    total_gases_distintos =total_gases_distintos ,gas_top =gas_top )


    # ================= GESTIÃ“N DE RESIDUOS (EL EXTRACTOR MÃGICO) =================
@app .route ('/residuos',methods =['GET'])
def residuos_dashboard ():
    if 'user_id'not in session :return redirect ("/")
    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )

    cursor .execute ("SELECT SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Residuos'",(empresa ,))
    res =cursor .fetchone ()
    total =float (res ['total'])if res and res ['total']else 0.0 

    cursor .execute ("SELECT categoria, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Residuos' GROUP BY categoria",(empresa ,))
    datos_cat =[{'categoria':row ['categoria'],'total':float (row ['total'])if row ['total']else 0.0 }for row in cursor .fetchall ()]

    cursor .execute ("SELECT SUBSTRING(fecha, 1, 7) as mes, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Residuos' GROUP BY mes ORDER BY mes",(empresa ,))
    datos_mes =[{'mes':row ['mes'],'total':float (row ['total'])if row ['total']else 0.0 }for row in cursor .fetchall ()]

    cursor .execute ("""
        SELECT COALESCE(NULLIF(TRIM(actividad), ''), 'Sin especificar') as trat,
               SUM(emision) as total
        FROM registros WHERE empresa = %s AND fuente = 'Residuos'
        GROUP BY 1 ORDER BY 2 DESC
    """,(empresa ,))
    datos_tratamiento =[{'tratamiento':row ['trat'],'total':float (row ['total'])if row ['total']else 0.0 }for row in cursor .fetchall ()]

    conn .close ()
    return render_template ("residuos.html",total_emision =total ,datos_categoria =datos_cat ,
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
    (['coincineracion','coincineraciÃ³n','combustion','combustiÃ³n','incineracion','incineraciÃ³n'],'CombustiÃ³n'),
    (['digestion anaerobica','digestiÃ³n anaerÃ³bica','anaerobica','anaerÃ³bica'],'DigestiÃ³n anaerÃ³bica'),
    (['pretratamiento'],'Pretratamiento'),
    (['aplicacion a suelo','aplicaciÃ³n a suelo'],'AplicaciÃ³n a suelo'),
    ]

    def normalizar_tratamiento (texto ):
        t =limpiar_texto (texto )
        if not t or t in ('nan','none'):
            return ''
        for palabras_clave ,nombre_normalizado in _TRAT_SINONIMOS :
            if any (p in t for p in palabras_clave ):
                return nombre_normalizado 
        return texto .strip ()

    def get_factores_residuos ():
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
        'residuo','residuos','waste','papel','carton','cartÃ³n','plastico','plÃ¡stico',
        'vidrio','metal','organico','orgÃ¡nico','wood','glass','batteries','bateria',
        'baterias','weee','clothing','tyres','neumaticos','neumÃ¡ticos','mineral',
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
        return unique 

    def buscar_factor (residuo_limpio ,factores_db ,anio =0 ,tratamiento =None ):
        residuo_norm =limpiar_texto (residuo_limpio )
        defra_cat =clasificar_defra (residuo_limpio )
        defra_norm =limpiar_texto (defra_cat )
        trat_norm =limpiar_texto (tratamiento or '')
        def _hit (f ):
            return float (f ['factor']),defra_cat ,f .get ('nombre_chile','')or '',f .get ('tratamiento','')or ''
        stopwords ={'de','y','en','para','los','las','el','la','con','sin','tipo',
        'residuos','residuo','envases','mezcla','mezclas','otros','materiales'}
        palabras_pdf =set (w for w in residuo_norm .replace (',',' ').replace ('.',' ').split ()
        if w not in stopwords and len (w )>2 )
        def _trat_match (f_trat ):
            if not trat_norm or not f_trat :
                return False 
            f_trat_n =limpiar_texto (f_trat )
            return trat_norm in f_trat_n or f_trat_n in trat_norm 
        def _search (db ,require_trat =False ):
        # Primera pasada: match exacto por categorÃ­a DEFRA (con tratamiento si aplica)
            for f in db :
                if limpiar_texto (str (f ['categoria']))==defra_norm :
                    if not require_trat or _trat_match (f .get ('tratamiento','')):
                        return _hit (f )
                        # Segunda pasada: match parcial por nombre de categorÃ­a
            for f in db :
                cat_db_norm =limpiar_texto (f ['categoria'])
                coincide_cat =(cat_db_norm ==residuo_norm or cat_db_norm in residuo_norm 
                or residuo_norm in cat_db_norm )
                if not coincide_cat :
                    palabras_db =set (w for w in cat_db_norm .replace (',',' ').replace ('.',' ').split ()
                    if w not in stopwords and len (w )>2 )
                    coincide_cat =bool (palabras_db and palabras_db .intersection (palabras_pdf ))
                if coincide_cat :
                    if not require_trat or _trat_match (f .get ('tratamiento','')):
                        return _hit (f )
            return None 
            # Buscar primero con anio y tratamiento, luego sin tratamiento, luego sin anio
        for pool in (
        [f for f in factores_db if (f .get ('anio')or 0 )==anio ]if anio else [],
        factores_db ,
        ):
            if not pool :
                continue 
            if trat_norm :
                result =_search (pool ,require_trat =True )
                if result :
                    return result 
            result =_search (pool ,require_trat =False )
            if result :
                return result 
        return (0.0 ,defra_cat ,'','')

    if request .method =="POST":
        tipo_ingreso =request .form .get ("tipo_ingreso","manual")
        empresa =session .get ('empresa')

        # === PASO 2: enviar a revisiÃ³n del admin ===
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
                flash ("Tu archivo fue enviado para revisiÃ³n. El administrador lo validarÃ¡ y recibirÃ¡s confirmaciÃ³n pronto.","info")
            except Exception as e :
                flash (f"Error al enviar: {str (e )}","danger")
            return redirect ("/residuos")

            # === PASO 1: procesar PDF y mostrar preview ===
        if tipo_ingreso in ['sinader','sidrep']:
            archivo_pdf =request .files .get ("archivo_pdf")
            if not archivo_pdf or archivo_pdf .filename =='':
                flash ("Debes subir un archivo PDF vÃ¡lido.","danger")
                return redirect (request .url )
            try :
                temp_path =os .path .join (tempfile .gettempdir (),archivo_pdf .filename )
                archivo_pdf .save (temp_path )
                if tipo_ingreso =='sinader':
                    df_extraido =extract_sinader_data (temp_path )
                    col_res ,col_trat ,col_cant ,col_dest ='Residuo','Tipo Tratamiento','Cantidad (kg)','Destino'
                else :
                    df_extraido =extract_sidrep_data (temp_path )
                    col_res ,col_trat ,col_cant ,col_dest ='DescripciÃ³n Residuo','Estado del Residuo','Cantidad (Kg)','Empresa destinataria'
                os .remove (temp_path )

                conn =get_db ()
                cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
                cursor .execute ("SELECT categoria, factor, nombre_chile, tratamiento, COALESCE(anio, 0) as anio FROM factores")
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
                    elif 'FechaDeclaraciÃ³n'in df_extraido .columns and len (df_extraido )>0 :
                    # SIDREP: FechaDeclaraciÃ³n tiene formato YYYY-MM-DD
                        fecha_str =str (df_extraido ['FechaDeclaraciÃ³n'].iloc [0 ])
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

                    if tipo_ingreso =='sidrep':
                        trat_pdf ='Vertedero'
                    else :
                        trat_pdf =normalizar_tratamiento (str (row .get (col_trat ,'')))
                    factor ,defra_cat ,nombre_chile ,tratamiento_defra =buscar_factor (
                    residuo_limpio ,factores_db ,anio =anio_pdf ,tratamiento =trat_pdf )
                    if not trat_pdf :
                        trat_pdf =tratamiento_defra or ''
                    emision =round ((cantidad /1000 )*factor ,4 )
                    preview_rows .append ({
                    'fecha':fecha_registro_pdf ,
                    'destino':(destino or 'Operaciones')[:50 ],
                    'categoria':residuo_limpio ,
                    'nombre_chile':nombre_chile ,
                    'tratamiento':trat_pdf ,
                    'cantidad':cantidad ,
                    'factor':factor ,
                    'emision':emision ,
                    'defra_cat':defra_cat ,
                    })

                conn2 =get_db ()
                cur2 =conn2 .cursor ()
                cur2 .execute ("SELECT DISTINCT tratamiento FROM factores WHERE tratamiento IS NOT NULL AND tratamiento != '' ORDER BY tratamiento")
                tratamientos_disponibles =[r [0 ]for r in cur2 .fetchall ()]
                conn2 .close ()

                return render_template ("formulario_residuos.html",
                factores =get_factores_residuos (),
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

        # Preload factors: {(categoria, tratamiento_lower, anio): factor}
        cursor .execute ("SELECT categoria, COALESCE(tratamiento, '') as trat, COALESCE(anio, 0) as anio, factor FROM factores")
        _fac_rows =cursor .fetchall ()
        factores_lookup ={}
        for _cat ,_trat ,_anio ,_fac in _fac_rows :
            factores_lookup [(_cat ,_trat .lower (),_anio )]=_fac 
            if (_cat ,_trat .lower (),0 )not in factores_lookup :
                factores_lookup [(_cat ,_trat .lower (),0 )]=_fac 
                # Fallback sin tratamiento
            if (_cat ,'',_anio )not in factores_lookup :
                factores_lookup [(_cat ,'',_anio )]=_fac 
            if (_cat ,'',0 )not in factores_lookup :
                factores_lookup [(_cat ,'',0 )]=_fac 

        filas_guardadas =0 
        for i in range (len (periodos )):
            if not periodos [i ].strip ()or not cantidades [i ].strip ():continue 
            fecha_limpia =f"{periodos [i ]}-01"
            try :
                cant =float (cantidades [i ].replace (',','.'))
                anio_periodo =int (periodos [i ][:4 ])if len (periodos [i ])>=4 else datetime .now ().year 
                trat_raw =normalizar_tratamiento (tratamientos [i ]if i <len (tratamientos )else '')
                trat_key =trat_raw .lower ()
                fac =(factores_lookup .get ((tipos [i ],trat_key ,anio_periodo ))
                or factores_lookup .get ((tipos [i ],trat_key ,0 ))
                or factores_lookup .get ((tipos [i ],'',anio_periodo ))
                or factores_lookup .get ((tipos [i ],'',0 ))
                or float (factores_filas [i ].replace (',','.')if i <len (factores_filas )else 0 ))
            except :cant ,fac =0.0 ,0.0 
            emision =(cant /1000 )*fac 
            destino =destinos [i ]if i <len (destinos )else ''
            cursor .execute ("""
                INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,(fecha_limpia ,empresa ,(destino or 'Operaciones')[:50 ],'Alcance 3','Residuos',
            tipos [i ],trat_raw ,'kg',cant ,fac ,emision ))
            filas_guardadas +=1 

        conn .commit ()
        conn .close ()
        flash (f"Se han guardado {filas_guardadas } registro(s) manuales exitosamente.","success")
        return redirect ("/residuos")

    return render_template ("formulario_residuos.html",factores =get_factores_residuos ())

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


def _resolver_datos_alcance1 (form ):
    categoria =form .get ('combustible')
    unidad =form .get ('unidad')
    actividad ="Consumo general"

    if categoria in (None ,''):
        return None ,unidad ,actividad ,0.0
    if categoria =='Otros':
        categoria =form .get ('otro_combustible')
        factor =_parse_float_input (form .get ('otro_factor','0'))
    else :
        factor =_parse_float_input (form .get ('factor_oculto','0'))

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
):
    cursor .execute (
    """
        INSERT INTO agua_flujos
        (empresa, sede_id, periodo, tipo_flujo, fuente_agua, destino_agua, volumen_m3,
         proceso_o_area, retorna_mismo_sistema_hidrico, tiene_tratamiento, calidad_dato,
         evidencia, observaciones, fecha_registro)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
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
    ),
    )


def _calcular_factores_electricos (cursor ,sistema ,anio_reg ,mes_reg ,origen ,tiene_irec ):
    cursor .execute (
    "SELECT factor_emision_avg FROM factores_electricos WHERE sistema = %s AND anio = %s AND mes = %s",
    (sistema ,anio_reg ,mes_reg )
    )
    res_ub =cursor .fetchone ()
    if not res_ub :
        cursor .execute (
        "SELECT factor_emision_avg FROM factores_electricos WHERE sistema = %s ORDER BY anio DESC, mes DESC LIMIT 1",
        (sistema ,)
        )
        res_ub =cursor .fetchone ()
    factor_ubicacion =float (res_ub [0 ])if res_ub and res_ub [0 ]is not None else 0.0 

    if origen =='ERNC'and tiene_irec =='Si':
        factor_mercado =0.0 
    else :
        cursor .execute (
        "SELECT factor_emision_avg FROM factores_electricos WHERE LOWER(sistema) = 'residual' AND anio = %s AND mes = %s",
        (anio_reg ,mes_reg )
        )
        res_merc =cursor .fetchone ()
        if not res_merc :
            cursor .execute (
            "SELECT factor_emision_avg FROM factores_electricos WHERE LOWER(sistema) = 'residual' ORDER BY anio DESC, mes DESC LIMIT 1"
            )
            res_merc =cursor .fetchone ()
        if not res_merc :
            cursor .execute (
            "SELECT factor_emision_avg FROM factores_electricos WHERE sistema = %s ORDER BY ABS(anio - %s) ASC, ABS(mes - %s) ASC LIMIT 1",
            (sistema ,anio_reg ,mes_reg )
            )
            res_merc =cursor .fetchone ()
        factor_mercado =float (res_merc [0 ])if res_merc and res_merc [0 ]is not None else 0.0 

    return factor_ubicacion ,factor_mercado 


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

        cantidad =_parse_float_input (request .form .get ('cantidad','0'))

        conn =get_db ()
        cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )

        if alcance =='Agua':
            periodo_agua =request .form .get ('periodo')or None 
            tipo_flujo_agua =request .form .get ('tipo_flujo','captacion')
            modo_registro_agua =request .form .get ('modo_registro','individual')
            volumen_agua =_parse_float_input (request .form .get ('volumen_m3','0'))
            if modo_registro_agua =='multiple'and not periodo_agua :
                periodo_agua =request .form .get ('fecha_inicio')
            if not periodo_agua :
                conn .close ()
                flash ("Debes seleccionar el perÃ­odo del registro de agua.","error")
                return redirect (url_for ("registro",tab ="agua"))
            if tipo_flujo_agua not in ('captacion','retorno','reuso'):
                conn .close ()
                flash ("El tipo de flujo no es vÃ¡lido.","error")
                return redirect (url_for ("registro",tab ="agua"))
            try :
                periodo_agua =datetime .strptime (f"{periodo_agua }-01","%Y-%m-%d").date ()
            except ValueError :
                conn .close ()
                flash ("El perÃ­odo de agua no tiene un formato vÃ¡lido.","error")
                return redirect (url_for ("registro",tab ="agua"))
            destino_agua =request .form .get ('destino_agua')if tipo_flujo_agua in ('retorno','reuso')else None 
            origen_hidrico =request .form .get ('origen_hidrico')if tipo_flujo_agua =='captacion'else None 
            valor_retorno =request .form .get ('retorna_mismo_sistema_hidrico','no_informado')
            retorna_mismo =None if valor_retorno =='no_informado'else valor_retorno =='si'
            valor_tratamiento =request .form .get ('tiene_tratamiento','no_informado')
            tiene_tratamiento =None if valor_tratamiento =='no_informado'else valor_tratamiento =='si'
            cantidades_lote =request .form .getlist ('batch_cantidad[]')
            if modo_registro_agua =='multiple':
                fecha_inicio =request .form .get ('fecha_inicio')
                fecha_fin =request .form .get ('fecha_fin')
                if not fecha_inicio or not fecha_fin :
                    conn .close ()
                    flash ("Debes seleccionar un mes de inicio y un mes de final.","error")
                    return redirect (url_for ("registro",tab ="agua"))
                try :
                    meses_lote =_meses_entre (fecha_inicio ,fecha_fin )
                except ValueError as exc :
                    conn .close ()
                    flash (str (exc ),"error")
                    return redirect (url_for ("registro",tab ="agua"))
                if len (cantidades_lote )!=len (meses_lote ):
                    conn .close ()
                    flash ("La tabla generada no coincide con el rango seleccionado.","error")
                    return redirect (url_for ("registro",tab ="agua"))
                try :
                    for idx ,mes in enumerate (meses_lote ):
                        cantidad_lote =_parse_float_input (cantidades_lote [idx ],None )
                        if cantidad_lote is None or cantidad_lote <0 :
                            raise ValueError (f"Cantidad invÃ¡lida para {mes }.")
                        fecha_lote =datetime .strptime (f"{mes }-01","%Y-%m-%d").date ()
                        cursor .execute ("""
                            INSERT INTO agua_flujos
                            (empresa, sede_id, periodo, tipo_flujo, fuente_agua, origen_hidrico, destino_agua, volumen_m3, proceso_o_area, retorna_mismo_sistema_hidrico, tiene_tratamiento, calidad_dato, evidencia, observaciones, fecha_registro)
                            VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        """,(
                        empresa ,fecha_lote ,tipo_flujo_agua ,
                        request .form .get ('fuente_agua'),origen_hidrico ,destino_agua ,cantidad_lote ,
                        request .form .get ('proceso_o_area'),retorna_mismo ,
                        tiene_tratamiento ,request .form .get ('calidad_dato'),
                        request .form .get ('evidencia'),request .form .get ('observaciones'),
                        ))
                    conn .commit ()
                except Exception as exc :
                    conn .rollback ()
                    conn .close ()
                    flash (f"No se pudo guardar el lote de agua: {exc }","error")
                    return redirect (url_for ("registro",tab ="agua"))
                conn .close ()
                flash (f"Se guardaron {len (meses_lote )} registros de agua entre {fecha_inicio } y {fecha_fin }.","success")
                return redirect ("/agua")
            if volumen_agua <0 :
                conn .close ()
                flash ("El volumen no puede ser negativo.","error")
                return redirect (url_for ("registro",tab ="agua"))
            try :
                cursor .execute ("""
                    INSERT INTO agua_flujos
                    (empresa, sede_id, periodo, tipo_flujo, fuente_agua, origen_hidrico, destino_agua, volumen_m3, proceso_o_area, retorna_mismo_sistema_hidrico, tiene_tratamiento, calidad_dato, evidencia, observaciones, fecha_registro)
                    VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,(
                empresa ,periodo_agua ,tipo_flujo_agua ,
                request .form .get ('fuente_agua'),origen_hidrico ,destino_agua ,volumen_agua ,
                request .form .get ('proceso_o_area'),retorna_mismo ,
                tiene_tratamiento ,request .form .get ('calidad_dato'),
                request .form .get ('evidencia'),request .form .get ('observaciones'),
                ))
                conn .commit ()
            except Exception as exc :
                conn .rollback ()
                conn .close ()
                flash (f"No se pudo guardar el registro de agua: {exc }","error")
                return redirect (url_for ("registro",tab ="agua"))
            conn .close ()
            flash ("Registro de agua guardado exitosamente.","success")
            return redirect ("/agua")

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
                flash ("Completa el Ã¡rea o instalaciÃ³n antes de guardar el lote.","error")
                return redirect ("/registro")

            if len (cantidades_lote )!=len (meses_lote ):
                conn .close ()
                flash ("La tabla generada no coincide con el rango seleccionado.","error")
                return redirect ("/registro")

            categoria ,unidad ,actividad ,factor =_resolver_datos_alcance1 (request .form )
            if not categoria or not unidad :
                conn .close ()
                flash ("Completa la categorÃ­a y la unidad antes de guardar el lote.","error")
                return redirect ("/registro")

            try :
                for idx ,mes in enumerate (meses_lote ):
                    cantidad_raw =str (cantidades_lote [idx ]).strip ()
                    if not cantidad_raw :
                        raise ValueError (f"Falta completar la cantidad del mes {mes }.")
                    cantidad_lote =float (cantidad_raw .replace (',','.'))
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
                flash ("Completa la instalaciÃ³n o sucursal antes de guardar el lote.","error")
                return redirect ("/registro")

            if not origen or not sistema :
                conn .close ()
                flash ("Completa el origen de energÃ­a y el sistema antes de guardar el lote.","error")
                return redirect ("/registro")

            if len (cantidades_lote )!=len (meses_lote ):
                conn .close ()
                flash ("La tabla generada no coincide con el rango seleccionado.","error")
                return redirect ("/registro")

            categoria =f"Electricidad {sistema }"
            unidad ="kWh"
            actividad ="Consumo de red elÃ©ctrica"

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
                    cantidad_lote =float (cantidad_raw .replace (',','.'))
                    fecha_lote =f"{mes }-01"
                    fecha_dt =datetime .strptime (fecha_lote ,"%Y-%m-%d")
                    anio_reg ,mes_reg =fecha_dt .year ,fecha_dt .month 
                    factor_ubicacion ,factor =_calcular_factores_electricos (cursor ,sistema ,anio_reg ,mes_reg ,origen ,tiene_irec )
                    emision_ubicacion =round (cantidad_lote *factor_ubicacion ,4 )
                    emision =round (cantidad_lote *factor ,4 )
                    cursor .execute ("""
                        INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision, emision_ubicacion, origen_energia, tiene_irec, sistema)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """,(fecha_lote ,empresa ,area ,alcance ,fuente ,categoria ,actividad ,unidad ,cantidad_lote ,factor ,emision ,emision_ubicacion ,origen ,tiene_irec ,sistema ))
                    calcular_huella_suministro_registro (cursor ,cursor .fetchone ()[0 ])
                conn .commit ()
            except Exception as exc :
                conn .rollback ()
                conn .close ()
                flash (f"No se pudo guardar el lote elÃ©ctrico: {exc }","error")
                return redirect ("/registro")

            conn .close ()
            flash (f"Se guardaron {len (meses_lote )} consumos elÃ©ctricos entre {fecha_inicio } y {fecha_fin }.","success")
            return redirect ("/electricidad")

        if alcance =='Alcance 2':
            origen =request .form .get ('origen_energia')
            sistema =request .form .get ('sistema_elec')
            tiene_irec =request .form .get ('tiene_irec','No')

            categoria =f"Electricidad {sistema }"
            unidad ="kWh"
            actividad ="Consumo de red elÃ©ctrica"

            fecha_dt =datetime .strptime (fecha ,"%Y-%m-%d")
            anio_reg ,mes_reg =fecha_dt .year ,fecha_dt .month 

            factor_ubicacion ,factor =_calcular_factores_electricos (cursor ,sistema ,anio_reg ,mes_reg ,origen ,tiene_irec )
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
            categoria ,unidad ,actividad ,factor =_resolver_datos_alcance1 (request .form )

            emision =cantidad *factor 

        if alcance =='Alcance 2':
            cursor .execute ("""
                INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision, emision_ubicacion, origen_energia, tiene_irec, sistema)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """,(fecha ,empresa ,area ,alcance ,fuente ,categoria ,actividad ,unidad ,cantidad ,factor ,emision ,emision_ubicacion ,origen ,tiene_irec ,sistema ))
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
            flash (f"Consumo elÃ©ctrico registrado â€” Sistema: {sistema } | Factor ubicaciÃ³n: {factor_ubicacion } | EmisiÃ³n mercado: {emision } kg | EmisiÃ³n ubicaciÃ³n: {emision_ubicacion } kg","success")
            return redirect ("/electricidad")
        flash ("Registro guardado exitosamente.","success")
        return redirect ("/dashboard")

    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
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

    return render_template ("registro.html",datos_factores =datos_agrupados )


    # ================= IMPORTACIÃ“N Y EXPORTACIÃ“N =================
def _parse_import_file (file ,factores_dict =None ):
    meses_dict ={
    'enero':'01','febrero':'02','marzo':'03','abril':'04',
    'mayo':'05','junio':'06','julio':'07','agosto':'08',
    'septiembre':'09','octubre':'10','noviembre':'11','diciembre':'12'
    }
    try :
        df =pd .read_excel (file ,sheet_name ='Datos Centralizados')
    except ValueError :
        raise ValueError ("El Excel debe contener una pestaÃ±a llamada 'Datos Centralizados'.")

    df .columns =df .columns .str .strip ()
    df ['Cantidad']=df ['Cantidad'].astype (str ).str .replace (',','.',regex =False )
    df ['Cantidad']=pd .to_numeric (df ['Cantidad'],errors ='coerce')

    col_factor ='Factor emisiÃ³n (kg COâ‚‚/u)'
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

            # Saltar fila de descripciÃ³n (texto largo en Mes) y fila de ejemplo del template
        mes_raw =get_texto ('Mes','')
        if len (mes_raw )>20 :
            continue 
        if (get_texto ('Identificador','')=='GEN-01'and 
        get_texto ('Tipo de Combustible','')=='DiÃ©sel'and 
        get_texto ('Sucursal','')=='Sede Central'):
            continue 

        mes_texto =mes_raw .lower ()
        anio_str =get_texto ('AÃ±o',str (datetime .now ().year )).replace ('.0','')

        # Validar anio
        try :
            anio_int =int (float (anio_str ))
            if anio_int <2000 or anio_int >2035 :
                errores .append (f"AÃ±o fuera de rango: {anio_str }")
        except (ValueError ,TypeError ):
            errores .append (f"AÃ±o invÃ¡lido: '{anio_str }'")
            anio_int =datetime .now ().year 
        anio =str (anio_int )

        if not mes_texto :
            errores .append ("Mes vacÃ­o")
        elif mes_texto not in meses_dict :
            errores .append (f"Mes invÃ¡lido: '{mes_texto }'")
        mes_num =meses_dict .get (mes_texto ,'01')
        fecha_sql =f"{anio }-{mes_num }-01"

        excel_level1 =get_texto ('Level 1','')
        excel_level2 =get_texto ('Level 2','')

        fuente ='Desconocida'
        if excel_level1 .lower ()=='combustibles':
            if 'fija'in excel_level2 .lower ():fuente ='CombustiÃ³n Fija'
            elif 'mÃ³vil'in excel_level2 .lower ()or 'movil'in excel_level2 .lower ():fuente ='Combustible MÃ³vil'
            else :fuente ='CombustiÃ³n Fija'
        elif excel_level1 .lower ()=='electricidad':fuente ='Electricidad'
        elif excel_level1 .lower ()=='refrigerantes':fuente ='Refrigerantes'
        elif excel_level1 .lower ()=='residuos':fuente ='Residuos'
        elif excel_level1 =='':
            errores .append ("Level 1 vacÃ­o")
        else :
            errores .append (f"Level 1 desconocido: '{excel_level1 }'")

        if fuente in ('CombustiÃ³n Fija','Combustible MÃ³vil','CombustiÃ³n Estacionaria','Refrigerantes','Fugas de Refrigerantes'):
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
            if categoria =='':categoria ='Red ElÃ©ctrica'
            if actividad =='':actividad =identificador if identificador !=''else 'Consumo General'

        if categoria =='':categoria ='Desconocida'
        if actividad =='':actividad ='No especificada'

        # Validar cantidad (NaN o vacÃ­o se trata como 0 para permitir registros mensuales sin consumo)
        if pd .isna (row ['Cantidad']):
            cantidad =0.0 
        else :
            cantidad =float (row ['Cantidad'])
            if cantidad <0 :
                errores .append (f"Cantidad negativa ({cantidad })")

                # Factor: columna Excel â†’ auto-lookup en catÃ¡logo â†’ 0
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
                advertencias .append (f"Factor completado automÃ¡ticamente desde catÃ¡logo ({factor } kg COâ‚‚/{unidad })")

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
        'emision':emision ,
        'valid':len (errores )==0 ,
        'error':'; '.join (errores )if errores else None ,
        'advertencia':'; '.join (advertencias )if advertencias else None 
        })

    return filas 


@app .route ("/descargar_plantilla")
def descargar_plantilla ():
    if 'user_id'not in session :
        return redirect ("/")

    columnas =[
    'Mes','AÃ±o','Level 1','Level 2','Scope','Sucursal',
    'Tipo de Combustible','Tipo Unidad de Consumo','Identificador',
    'Unidad de Medida','Cantidad','Factor emisiÃ³n (kg COâ‚‚/u)'
    ]
    descripciones =[
    'Nombre del mes (Enero, Febreroâ€¦)','AÃ±o (ej: 2025)',
    'Combustibles / Electricidad / Refrigerantes / Residuos',
    'CombustiÃ³n Fija / CombustiÃ³n MÃ³vil (solo Combustibles)',
    'Alcance 1 / Alcance 2 / Alcance 3',
    'Nombre de la sucursal o sede',
    'Tipo de combustible o gas refrigerante',
    'Tipo de consumo o proceso',
    'CÃ³digo o identificador del equipo/medidor',
    'Litros, kWh, kg, etc.',
    'Cantidad consumida (nÃºmero)',
    'Factor de emisiÃ³n oficial (kg COâ‚‚ por unidad)'
    ]
    ejemplo =['Enero',2025 ,'Combustibles','CombustiÃ³n Fija','Alcance 1','Sede Central','DiÃ©sel','Generador','GEN-01','Litros',1500.5 ,2.68 ]

    conn =get_db ()
    df_factores =pd .read_sql_query (
    "SELECT categoria as \"CategorÃ­a\", unidad as \"Unidad\", factor as \"Factor Oficial\" FROM factores ORDER BY categoria",
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

        col_widths =[12 ,8 ,18 ,22 ,12 ,18 ,24 ,24 ,16 ,16 ,12 ,26 ]
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
            df_factores .to_excel (writer ,sheet_name ='CatÃ¡logo Oficial',index =False )
            ws2 =writer .sheets ['CatÃ¡logo Oficial']
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
            flash ("No se seleccionÃ³ ningÃºn archivo","error")
            return redirect (request .url )

        file =request .files ['archivo']
        if not file or not (file .filename .endswith ('.xlsx')or file .filename .endswith ('.xls')):
            flash ("Formato no vÃ¡lido. Debe ser .xlsx","error")
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
        except ValueError as e :
            flash (str (e ),"error")
            return redirect (request .url )
        except Exception as e :
            flash (f"Error tÃ©cnico al procesar: {str (e )}","error")
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

    filas_validas =[f for f in filas if f .get ('valid')]
    if not filas_validas :
        flash ("No hay registros vÃ¡lidos para importar.","error")
        return redirect ("/importar")

    try :
        conn =get_db ()
        cursor =conn .cursor ()
        for fila in filas_validas :
            cursor .execute ("""
                INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,(fila ['fecha'],empresa ,fila ['area'],fila ['alcance'],fila ['fuente'],
            fila ['categoria'],fila ['actividad'],fila ['unidad'],
            fila ['cantidad'],fila ['factor'],fila ['emision']))
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
    conn .close ()
    return render_template ("exportar.html",empresa =empresa ,anios =anios ,alcances =alcances ,fuentes =fuentes )

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
    conn .close ()

    if df .empty :
        flash ("No hay registros que coincidan con los filtros seleccionados.","warning")
        return redirect ("/exportar")

    df .rename (columns ={
    'fecha':'Fecha','area':'Ãrea / InstalaciÃ³n','alcance':'Alcance',
    'fuente':'Fuente','categoria':'CategorÃ­a','actividad':'Actividad',
    'unidad':'Unidad','cantidad':'Cantidad','factor':'Factor (kg COâ‚‚/u)',
    'emision':'EmisiÃ³n (kg COâ‚‚e)'
    },inplace =True )

    from openpyxl .styles import Font ,PatternFill ,Alignment 
    output =io .BytesIO ()
    with pd .ExcelWriter (output ,engine ='openpyxl')as writer :
        df .to_excel (writer ,sheet_name ='Registros',index =False )
        ws =writer .sheets ['Registros']
        header_fill =PatternFill ("solid",fgColor ="1E40AF")
        header_font =Font (bold =True ,color ="FFFFFF")
        for cell in ws [1 ]:
            cell .fill =header_fill 
            cell .font =header_font 
            cell .alignment =Alignment (horizontal ='center')
        for col in ws .columns :
            ws .column_dimensions [col [0 ].column_letter ].width =20 

    output .seek (0 )
    sufijo =f"_{anio }"if anio else "_completo"
    nombre =f"Datos_GreenTrack_{empresa .replace (' ','_')}{sufijo }_{datetime .now ().strftime ('%Y%m%d')}.xlsx"
    return send_file (output ,download_name =nombre ,as_attachment =True )


    # ================= RUTAS ADMIN =================
def get_admin_stats ():
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("SELECT COUNT(*) FROM usuarios WHERE es_admin = 0")
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

def calcular_metricas_agua_admin (cursor ,empresas_filtro ):
    empresas_base =[emp ['empresa']for emp in empresas_filtro if emp .get ('empresa')]
    cursor .execute ("""
        SELECT empresa, SUBSTRING(periodo::text, 1, 4) AS anio,
               SUM(CASE WHEN tipo_flujo = 'captacion' THEN volumen_m3 ELSE 0 END) AS captacion,
               SUM(CASE WHEN tipo_flujo = 'retorno' AND retorna_mismo_sistema_hidrico = TRUE THEN volumen_m3 ELSE 0 END) AS retorno_mismo
        FROM agua_flujos
        WHERE empresa IS NOT NULL AND periodo IS NOT NULL
        GROUP BY empresa, anio
    """)
    agua_map ={}
    for row in cursor .fetchall ():
        emp ,anio_val =row ['empresa'],row ['anio']
        if emp and anio_val :
            azul =max (0.0 ,float ((row ['captacion']or 0 )-(row ['retorno_mismo']or 0 )))
            agua_map .setdefault (emp ,{})[anio_val ]=agua_map .get (emp ,{}).get (anio_val ,0 )+azul

    cursor .execute ("""
        SELECT empresa, SUBSTRING(fecha::text, 1, 4) AS anio,
               SUM(huella_suministro_m3) AS suministro
        FROM resultados_huella_suministro_agua
        WHERE empresa IS NOT NULL AND fecha IS NOT NULL AND huella_suministro_m3 IS NOT NULL
        GROUP BY empresa, anio
    """)
    for row in cursor .fetchall ():
        emp ,anio_val =row ['empresa'],row ['anio']
        if emp and anio_val :
            agua_map .setdefault (emp ,{})[anio_val ]=agua_map .get (emp ,{}).get (anio_val ,0 )+float (row ['suministro']or 0 )

    all_agua_empresas =empresas_base or sorted (agua_map .keys ())
    all_agua_anios =sorted ({a for d in agua_map .values ()for a in d .keys ()},reverse =True )
    chart_agua_data ={"Todos":[round (sum (agua_map .get (e ,{}).values ()),2 )for e in all_agua_empresas ]}
    for anio in all_agua_anios :
        chart_agua_data [anio ]=[round (agua_map .get (e ,{}).get (anio ,0 ),2 )for e in all_agua_empresas ]
    total_huella_hidrica_global =round (sum (chart_agua_data .get ("Todos",[])),2 )
    top_huella_hidrica =sorted (
    [{'empresa':e ,'total':round (sum (v .values ()),2 )}for e ,v in agua_map .items ()if round (sum (v .values ()),2 )>0 ],
    key =lambda x :-x ['total']
    )[:6 ]
    return agua_map ,all_agua_empresas ,all_agua_anios ,chart_agua_data ,total_huella_hidrica_global ,top_huella_hidrica

@app .route ("/admin/dashboard")
def admin_dashboard ():
    if 'user_id'not in session or session .get ('es_admin')!=1 :return redirect ("/")
    t_emp ,t_reg ,t_em ,t_pend =get_admin_stats ()

    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("SELECT id, empresa, email, contacto, rut, fecha_registro, sector_empresa, tipo_empresa FROM usuarios WHERE es_admin = 0 ORDER BY fecha_registro DESC LIMIT 5")
    ultimas =[dict (row )for row in cursor .fetchall ()]
    cursor .execute ("SELECT id, empresa, sector_empresa, tipo_empresa FROM usuarios WHERE es_admin = 0 ORDER BY empresa")
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
        SELECT empresa, anio, unidad, valor
        FROM medidas_productivas
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
        huella_anual =float (agua_map .get (emp ,{}).get (anio ,0 )or 0 )
        intensidad_huella =round (huella_anual /medida_valor ,6 )if medida_valor >0 else 0 
        intensidad_huella_series .setdefault (anio ,{})[emp ]=intensidad_huella 
        if emp not in intensidad_huella_latest :
            intensidad_huella_latest [emp ]=intensidad_huella 
    intensidad_series ['Todos']=intensidad_latest 
    intensidad_huella_series ['Todos']=intensidad_huella_latest 
    intensidad_empresas_values =[round (float (intensidad_latest .get (emp ['empresa'])or 0 ),6 )for emp in empresas_filtro ]
    intensidad_huella_values =[round (float (intensidad_huella_latest .get (emp ['empresa'])or 0 ),6 )for emp in empresas_filtro ]
    intensidad_series_values ={
    key :[round (float ((serie .get (emp ['empresa']))or 0 ),6 )for emp in empresas_filtro ]
    for key ,serie in intensidad_series .items ()
    }
    intensidad_huella_series_values ={
    key :[round (float ((serie .get (emp ['empresa']))or 0 ),6 )for emp in empresas_filtro ]
    for key ,serie in intensidad_huella_series .items ()
    }
    intensidad_years =sorted ([k for k in intensidad_series .keys ()if k !='Todos'],reverse =True )
    intensidad_huella_promedio =round (sum (v for v in intensidad_huella_values if v >0 )/len ([v for v in intensidad_huella_values if v >0 ]),6 )if any (v >0 for v in intensidad_huella_values )else 0 
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
    if session .get ('es_admin')!=1 :return redirect ("/")
    t_emp ,t_reg ,t_em ,t_pend =get_admin_stats ()
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("SELECT id, empresa, email, contacto, rut, fecha_registro, sector_empresa, tipo_empresa FROM usuarios WHERE es_admin = 0 ORDER BY empresa")
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
    for emp in empresas :
        medida =medidas_empresas .get (emp ['empresa'])if medidas_empresas else None 
        if medida and float (medida .get ('valor')or 0 )>0 :
            huella_anual =float (agua_map .get (emp ['empresa'],{}).get (str (medida ['anio']),0 )or 0 )
            intensidad_huella_vals .append (huella_anual /float (medida ['valor']))
    intensidad_huella_promedio =round (sum (intensidad_huella_vals )/len (intensidad_huella_vals ),6 )if intensidad_huella_vals else 0 
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
    # pandas convierte NULL a float('nan') â€” convertir a None para que Jinja2 los trate como falsy
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
def admin_huella_hidrica ():
    if not _huella_permiso_admin ():
        return redirect ("/")
    t_emp ,t_reg ,t_em ,t_pend =get_admin_stats ()
    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    cursor .execute ("SELECT id, empresa, nombre_sede, region, comuna, latitud, longitud, codigo_cuenca, nombre_cuenca, nivel_ubicacion, activo, fecha_creacion FROM agua_sedes ORDER BY empresa, nombre_sede")
    sedes_raw =[dict (r )for r in cursor .fetchall ()]
    sedes =[]
    for sede in sedes_raw :
        sede ["estado_ubicacion"]=_huella_estado_ubicacion (sede )
        sede ["factor_disponible"]=_huella_sede_factor_disponible (cursor ,sede )
        sedes .append (sede )

    cursor .execute ("SELECT * FROM factores_escasez_agua ORDER BY fecha_carga DESC, nivel_geografico, codigo_geografico")
    factores =[dict (r )for r in cursor .fetchall ()]

    cursor .execute ("""
        SELECT empresa, sede_id, periodo,
               SUM(CASE WHEN tipo_flujo = 'captacion' THEN volumen_m3 ELSE 0 END) AS captacion_m3,
               SUM(CASE WHEN tipo_flujo = 'retorno' THEN volumen_m3 ELSE 0 END) AS retorno_m3,
               SUM(CASE WHEN tipo_flujo = 'retorno' AND retorna_mismo_sistema_hidrico = TRUE THEN volumen_m3 ELSE 0 END) AS retorno_mismo_sistema_m3,
               SUM(CASE WHEN tipo_flujo = 'reuso' THEN volumen_m3 ELSE 0 END) AS reuso_m3
        FROM agua_flujos
        GROUP BY empresa, sede_id, periodo
        ORDER BY periodo DESC, empresa
    """)
    coberturas =[dict (r )for r in cursor .fetchall ()]
    sede_map ={s ["id"]:s for s in sedes }
    cobertura_rows =[]
    for row in coberturas :
        sede =sede_map .get (row ["sede_id"],{})
        factor_disponible =_huella_sede_factor_disponible (cursor ,sede )if sede else False 
        cobertura_rows .append ({
        **row ,
        "nombre_sede":sede .get ("nombre_sede","â€”"),
        "ubicacion_configurada":_huella_estado_ubicacion (sede )if sede else "Pendiente",
        "factor_disponible":factor_disponible ,
        "resultado_calculable":"SÃ­"if (factor_disponible and (row .get ("captacion_m3")or 0 )>0 )else "Pendiente",
        })
    conn .close ()
    return render_template (
    "admin_huella_hidrica.html",
    total_empresas =t_emp ,
    total_registros =t_reg ,
    total_emisiones =t_em ,
    total_pendientes =t_pend ,
    sedes =sedes ,
    factores =factores ,
    coberturas =cobertura_rows ,
    admin_section ="huella_hidrica",
    )

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
                flash ("Completa mÃ©todo, versiÃ³n, nivel geogrÃ¡fico, cÃ³digo, factor y fuente.","error")
            else :
                factor_val =float (factor_m3eq_m3 .replace (",","."))
                if factor_val <=0 :
                    raise ValueError ("El factor debe ser mayor que cero.")
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
        flash (f"No se pudo cargar la informaciÃ³n: {e }","danger")
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
    if session .get ('es_admin')!=1 :return redirect ("/")
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
        cursor .execute ("SELECT empresa FROM usuarios WHERE id = %s AND es_admin = 0",(empresa_id ,))
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
            for tabla in ['registros','combustible_movil','vehiculos','pdf_uploads',
            'agua_consumo','agua_cuencas','agua_afluentes','agua_costos',
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
        flash ("Error: ese email ya estÃ¡ en uso por otra cuenta.","error")
    except Exception as e :
        conn .rollback ()
        flash (f"Error al editar: {e }","danger")
    finally :
        conn .close ()
    return redirect (url_for ('admin_empresas'))


@app .route ("/admin/resetear_password/<int:empresa_id>",methods =["POST"])
def admin_resetear_password (empresa_id ):
    if session .get ('es_admin')!=1 :return redirect ("/")
    nueva =request .form .get ("nueva_password","").strip ()
    if len (nueva )<6 :
        flash ("La contraseÃ±a debe tener al menos 6 caracteres.","error")
        return redirect (url_for ('admin_empresas'))
    conn =get_db ()
    cursor =conn .cursor ()
    try :
        cursor .execute ("SELECT empresa FROM usuarios WHERE id = %s AND es_admin = 0",(empresa_id ,))
        row =cursor .fetchone ()
        if not row :
            flash ("Empresa no encontrada.","error")
            conn .close ()
            return redirect (url_for ('admin_empresas'))
        cursor .execute ("UPDATE usuarios SET password = %s WHERE id = %s",(hash_password (nueva ),empresa_id ))
        conn .commit ()
        flash (f"ContraseÃ±a de '{row [0 ]}' reseteada correctamente.","success")
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
        cursor .execute ("SELECT empresa FROM usuarios WHERE id = %s AND es_admin = 0",(empresa_id ,))
        row =cursor .fetchone ()
        if not row :
            flash ("Empresa no encontrada.","error")
            conn .close ()
            return redirect (url_for ('admin_empresas'))
        nombre =row [0 ]
        for tabla in ['registros','combustible_movil','vehiculos','pdf_uploads',
        'agua_consumo','agua_cuencas','agua_afluentes','agua_costos',
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
    if session .get ('es_admin')!=1 :return redirect ("/")
    empresa ,email =request .form .get ("empresa"),request .form .get ("email")
    password =hash_password (request .form .get ("password"))
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
        flash ("Error: El correo electrÃ³nico ya estÃ¡ en uso.","error")
    finally :conn .close ()
    return redirect (url_for ('admin_empresas'))

@app .route ("/admin/debug_excel",methods =["POST"])
def admin_debug_excel ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    file =request .files .get ('archivo_factores')
    if not file or file .filename =='':
        return "No se subiÃ³ archivo",400 
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
            cell =str (val )if pd .notna (val )else '<span style="color:#ccc">â€”</span>'
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
        flash ("No se subiÃ³ ningÃºn archivo","danger")
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

            # Ãndices fijos confirmados para la hoja Equivalencias
            # Col 2=cat combustible, 5=unidad, 7=factor comb
            # Col 12=cat refrigerante, 14=factor ref
            # Col 19=categorÃ­a DEFRA residuo, 21=tratamiento, 22=equivalencia Chile, 23=factor residuo
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

                # Residuos: guardar un registro por (DEFRA, tratamiento) â€” no colapsar al mÃ¡ximo
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
        flash (f"Â¡SincronizaciÃ³n exitosa! Se actualizaron {actualizados } factores.","success")
    except Exception as e :
        flash (f"Error tÃ©cnico al leer el Excel. Detalle: {str (e )}","danger")
    return redirect (request .referrer )

@app .route ("/admin/preview_electricidad",methods =["POST"])
def preview_electricidad ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    archivo =request .files .get ("archivo_electricidad")
    if not archivo or archivo .filename =="":
        flash ("No se seleccionÃ³ ningÃºn archivo.","danger")
        return redirect (url_for ('admin_factores'))
    try :
        df =pd .read_excel (archivo ,sheet_name ='Factores elÃ©ctricos')
        filas =[]
        errores =[]
        for idx ,row in df .iterrows ():
            if pd .notna (row .iloc [0 ])and pd .notna (row .iloc [3 ]):
                try :
                    filas .append ({
                    'anio':int (row .iloc [0 ]),
                    'mes':int (row .iloc [1 ]),
                    'sistema':str (row .iloc [2 ]).strip (),
                    'factor':float (row .iloc [3 ])
                    })
                except (ValueError ,TypeError ):
                    errores .append (f"Fila {idx +2 }: valor invÃ¡lido â€” {list (row [:4 ])}")
        sistemas =sorted (set (f ['sistema']for f in filas ))
        anios =sorted (set (f ['anio']for f in filas ))
        return render_template ("admin_preview_electricidad.html",
        filas =filas ,errores =errores ,
        sistemas =sistemas ,anios =anios ,
        datos_json =json .dumps (filas ))
    except Exception as e :
        flash (f"Error al leer el Excel: {str (e )}","danger")
        return redirect (url_for ('admin_factores'))

@app .route ("/admin/cargar_electricidad",methods =["POST"])
def cargar_electricidad ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    datos_json =request .form .get ('datos_json')
    if not datos_json :
    # Carga directa legacy (sin preview)
        archivo =request .files .get ("archivo_electricidad")
        if not archivo or archivo .filename =="":
            flash ("No se seleccionÃ³ ningÃºn archivo.","danger")
            return redirect (request .referrer )
        try :
            df =pd .read_excel (archivo ,sheet_name ='Factores elÃ©ctricos')
            filas =[]
            for _ ,row in df .iterrows ():
                if pd .notna (row .iloc [0 ])and pd .notna (row .iloc [3 ]):
                    try :
                        filas .append ({'anio':int (row .iloc [0 ]),'mes':int (row .iloc [1 ]),
                        'sistema':str (row .iloc [2 ]).strip (),'factor':float (row .iloc [3 ])})
                    except (ValueError ,TypeError ):continue 
            datos_json =json .dumps (filas )
        except Exception as e :
            flash (f"Error al leer el Excel: {str (e )}","danger")
            return redirect (request .referrer )
    try :
        filas =json .loads (datos_json )
        conn =get_db ()
        cursor =conn .cursor ()
        cursor .execute ("DELETE FROM factores_electricos")
        for f in filas :
            cursor .execute (
            "INSERT INTO factores_electricos (anio, mes, sistema, factor_emision_avg) VALUES (%s, %s, %s, %s)",
            (f ['anio'],f ['mes'],f ['sistema'],f ['factor'])
            )
        conn .commit ()
        conn .close ()
        flash (f"Â¡{len (filas )} factores elÃ©ctricos cargados correctamente!","success")
    except Exception as e :
        flash (f"Error al guardar: {str (e )}","danger")
    return redirect (url_for ('admin_factores'))

@app .route ("/admin/empresa/<string:nombre_empresa>")
def admin_detalle_empresa (nombre_empresa ):
    if session .get ('es_admin')!=1 :return redirect ("/")
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
    if session .get ('es_admin')!=1 :return redirect ("/")
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
        _case =("CASE WHEN fuente IN ('CombustiÃ³n Fija','Combustible MÃ³vil','CombustiÃ³n Estacionaria','Refrigerantes','Fugas de Refrigerantes') THEN 'Alcance 1'"
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
        return redirect (request .referrer )

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
        _case =("CASE WHEN fuente IN ('CombustiÃ³n Fija','Combustible MÃ³vil','CombustiÃ³n Estacionaria','Refrigerantes','Fugas de Refrigerantes') THEN 'Alcance 1'"
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
    "SELECT empresa FROM usuarios WHERE es_admin = 0 ORDER BY empresa",
    conn 
    )['empresa'].tolist ()
    conn .close ()

    if df_total .empty :
        flash ("No hay registros en la plataforma para exportar.","warning")
        return redirect (url_for ('admin_dashboard'))

    df_total .rename (columns ={
    'empresa':'Empresa','fecha':'Fecha','area':'Ãrea',
    'alcance':'Alcance','fuente':'Fuente','categoria':'Combustible/CategorÃ­a',
    'actividad':'Uso','cantidad':'Cantidad','unidad':'Unidad',
    'factor':'Factor','emision':'Emisiones (kg CO2)'
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


    # ================= OTROS MÃ“DULOS (VehÃ­culos) =================
@app .route ("/vehiculos")
def vehiculos ():
    if 'user_id'not in session :return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("SELECT * FROM vehiculos WHERE empresa = %s ORDER BY patente",(session .get ('empresa'),))
    vehiculos_data =cursor .fetchall ()
    conn .close ()
    return render_template ("vehiculos.html",vehiculos_data =vehiculos_data )

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
        data =request .get_json ()
        if not data .get ('patente'):return jsonify ({"success":False ,"message":"Patente obligatoria"}),400 
        conn =get_db ()
        cursor =conn .cursor ()
        cursor .execute ("SELECT id FROM vehiculos WHERE empresa = %s AND patente = %s",(empresa ,data ['patente'].upper ()))
        if cursor .fetchone ():
            conn .close ()
            return jsonify ({"success":False ,"message":"Patente ya registrada"}),400 
        cursor .execute ("""
            INSERT INTO vehiculos (empresa, patente, tipo, marca, modelo, anio, fecha_registro)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id;
        """,(empresa ,data ['patente'].upper (),data .get ('tipo'),data .get ('marca'),data .get ('modelo'),data .get ('anio'),datetime .now ().strftime ("%Y-%m-%d %H:%M")))
        vehiculo_id =cursor .fetchone ()[0 ]
        conn .commit ()
        conn .close ()
        return jsonify ({"success":True ,"message":"VehÃ­culo guardado","vehiculo":{"id":vehiculo_id ,"patente":data ['patente'].upper ()}})

@app .route ("/vehiculos/eliminar/<int:id>")
def eliminar_vehiculo (id ):
    if 'user_id'not in session :return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("DELETE FROM vehiculos WHERE id = %s AND empresa = %s",(id ,session .get ('empresa')))
    conn .commit ()
    conn .close ()
    flash ("VehÃ­culo eliminado correctamente","info")
    return redirect (url_for ('vehiculos'))

@app .route ("/combustible/movil/eliminar/<int:id>")
def eliminar_registro_combustible (id ):
    if 'user_id'not in session :return redirect ("/")
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("DELETE FROM combustible_movil WHERE id = %s AND empresa = %s",(id ,session .get ('empresa')))
    conn .commit ()
    conn .close ()
    flash ("Registro de combustible eliminado","info")
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
    data =request .get_json ()
    if not data or not data .get ('registros'):
        return jsonify ({"success":False ,"message":"No se recibieron registros"}),400 

    factores_movil ={
    'diesel':2.68 ,'bencina':2.31 ,'gas_natural':2.02 ,
    'glp':1.61 ,'electricidad':0.233 ,'otro':2.5 
    }

    conn =get_db ()
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    guardados =0 
    errores =[]

    for i ,registro in enumerate (data .get ('registros',[])):
        try :
            vehiculo_id =int (registro ['vehiculo_id'])
            periodo =registro ['periodo']# YYYY-MM
            if not periodo :
                errores .append (f"Registro {i +1 }: periodo es obligatorio")
                continue 
            cantidad =float (registro ['cantidad'])
            if cantidad <=0 :
                errores .append (f"Registro {i +1 }: cantidad debe ser mayor a 0")
                continue 

            combustible =registro ['combustible']
            unidad =registro ['unidad']
            costo =float (registro .get ('costo')or 0 )
            fecha =f"{periodo }-01"
            factor =factores_movil .get (combustible .lower (),2.5 )
            emision =round (cantidad *factor ,4 )

            # Obtener patente del vehÃ­culo para registros
            cursor .execute ("SELECT patente, tipo FROM vehiculos WHERE id = %s AND empresa = %s",(vehiculo_id ,empresa ))
            vehiculo =cursor .fetchone ()
            patente =vehiculo ['patente']if vehiculo else f"VehÃ­culo {vehiculo_id }"
            tipo_v =vehiculo ['tipo']if vehiculo else ''
            actividad =f"{patente } ({tipo_v })"if tipo_v else patente 

            # 1. Insertar en registros (tabla maestra de emisiones)
            cursor .execute ("""
                INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,(fecha ,empresa ,'Flota Vehicular','Alcance 1',
            'Combustible MÃ³vil',combustible ,actividad ,
            unidad ,cantidad ,factor ,emision ))

            # 2. Insertar en combustible_movil (para tracking por vehÃ­culo)
            cursor .execute ("""
                INSERT INTO combustible_movil (empresa, vehiculo_id, periodo, combustible, cantidad, unidad, costo, fecha_registro)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,(empresa ,vehiculo_id ,periodo ,combustible ,cantidad ,unidad ,costo ,fecha ))

            guardados +=1 
        except Exception as e :
            conn .rollback ()
            errores .append (f"Registro {i +1 }: {str (e )}")

    if guardados >0 :
        conn .commit ()
    conn .close ()
    if guardados ==0 and errores :
        return jsonify ({"success":False ,"message":"No se guardÃ³ ningÃºn registro. Errores: "+"; ".join (errores )})
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
    cantidad =float (request .form .get ('cantidad',0 )or 0 )
    costo =float (request .form .get ('costo',0 )or 0 )
    periodo =request .form .get ('periodo','')
    uso_final =request .form .get ('uso_final','')

    factores_fijo ={
    'diesel':2.68 ,'petroleo':2.96 ,'gas_natural':2.02 ,
    'carbon':2.42 ,'gas_lp':1.61 ,'otro':2.5 
    }
    factor =factores_fijo .get (combustible ,2.5 )
    emision =cantidad *factor 

    fecha =(periodo +'-01')if periodo else datetime .now ().strftime ('%Y-%m-%d')

    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("""
        INSERT INTO registros (fecha, empresa, fuente, categoria, actividad, identificador, unidad, cantidad, costo, factor, emision, alcance)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """,(fecha ,empresa ,'CombustiÃ³n Fija',combustible ,uso_final or 'CombustiÃ³n estacionaria',
    unidad_consumo ,unidad ,cantidad ,costo ,factor ,emision ,'Alcance 1'))
    conn .commit ()
    conn .close ()
    flash ("Registro de combustible fijo guardado correctamente.","success")
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
        flash ("Las contraseÃ±as nuevas no coinciden.","error")
        return redirect (url_for ('mis_datos'))
    if len (nueva )<6 :
        flash ("La contraseÃ±a debe tener al menos 6 caracteres.","error")
        return redirect (url_for ('mis_datos'))
    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("SELECT password FROM usuarios WHERE id = %s",(session ['user_id'],))
    row =cursor .fetchone ()
    if not row or not verify_password (actual ,row [0 ]):
        conn .close ()
        flash ("La contraseÃ±a actual es incorrecta.","error")
        return redirect (url_for ('mis_datos'))
    cursor .execute ("UPDATE usuarios SET password = %s WHERE id = %s",(hash_password (nueva ),session ['user_id']))
    conn .commit ()
    conn .close ()
    flash ("ContraseÃ±a actualizada correctamente.","success")
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
        anio_sel =request .form .get ("medida_anio",str (current_year )).strip ()or str (current_year )
        try :
            unidad_medida =request .form .get ("medida_unidad","").strip ()
            valor_medida =float (str (request .form .get ("medida_valor","0")).replace (",","."))
            info_por_unidad =1 if request .form .get ("info_unidad")else 0 
            vehiculos =1 if request .form .get ("vehiculos","no")=="si"else 0 
            combustible_colaboradores =1 if request .form .get ("colaboradores","no")=="si"else 0 
            tarjeta_combustible =1 if request .form .get ("tarjeta","no")=="si"else 0 
            energeticos_sel =request .form .getlist ("energeticos[]")

            cursor .execute ("""
                INSERT INTO configuracion (empresa, info_por_unidad, vehiculos, combustible_colaboradores, tarjeta_combustible)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (empresa) DO UPDATE SET
                    info_por_unidad = EXCLUDED.info_por_unidad,
                    vehiculos = EXCLUDED.vehiculos,
                    combustible_colaboradores = EXCLUDED.combustible_colaboradores,
                    tarjeta_combustible = EXCLUDED.tarjeta_combustible
            """,(empresa ,info_por_unidad ,vehiculos ,combustible_colaboradores ,tarjeta_combustible ))

            cursor .execute ("DELETE FROM energeticos_empresa WHERE empresa = %s",(empresa ,))
            for energetico in energeticos_sel :
                proveedor =request .form .get (f"proveedor_{energetico }","").strip ()
                cursor .execute ("""
                    INSERT INTO energeticos_empresa (empresa, energetico, proveedor, documento)
                    VALUES (%s, %s, %s, %s)
                """,(empresa ,energetico ,proveedor ,None ))

            guardar_medida_productiva (cursor ,empresa ,int (anio_sel ),unidad_medida ,valor_medida )
            conn .commit ()
            flash ("ConfiguraciÃ³n guardada correctamente.","success")
        except Exception as e :
            conn .rollback ()
            flash (f"No se pudo guardar la configuraciÃ³n: {e }","error")
        finally :
            conn .close ()
        return redirect (url_for ('configuracion',anio =anio_sel ))

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
    cursor .execute ("SELECT anio, unidad, valor, fecha_actualizacion FROM medidas_productivas WHERE empresa = %s AND anio = %s",(empresa ,anio_sel ))
    medida_actual =cursor .fetchone ()
    if medida_actual :
        medida_actual =dict (medida_actual )
        total_t ,intensidad =calcular_intensidad_emisiones (conn ,empresa ,anio_sel ,medida_actual .get ('valor',0 ))
        medida_actual ['emisiones_anio_tco2e']=total_t 
        medida_actual ['intensidad_emisiones']=intensidad 
    medidas_historial =obtener_medidas_productivas (conn ,empresa )
    cursor .execute ("SELECT sector_empresa, tipo_empresa FROM usuarios WHERE empresa = %s",(empresa ,))
    perfil_empresa =cursor .fetchone ()
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
    anios_medidas =sorted (set (anios_medidas ),reverse =True ),
    anio_seleccionado =anio_sel ,
    perfil_empresa =perfil_empresa ,
    unidades_productivas =UNIDADES_PRODUCTIVAS 
    )

@app .route ("/agua/registro",methods =["GET","POST"])
def agua_registro ():
    if 'user_id'not in session :
        return redirect ("/")
    return render_template ("agua_registro.html")
@app .route ("/agua/reporte")
def agua_reporte ():
    if 'user_id'not in session :
        return redirect ("/")
    return render_template ("agua_reporte.html")


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
            periodo =datetime .strptime (f"{periodo_raw }-01","%Y-%m-%d").date ()
        except ValueError :
            conn .close ()
            flash ("Debes seleccionar un mes de registro vÃ¡lido.","error")
            return render_template ("agua_retorno_reuso.html",sede_predeterminada =sede_predeterminada )

        tipo_flujo =request .form .get ("tipo_flujo","retorno")
        if tipo_flujo not in ("retorno","reuso"):
            conn .close ()
            flash ("El tipo de flujo no es vÃ¡lido.","error")
            return render_template ("agua_retorno_reuso.html",sede_predeterminada =sede_predeterminada )

        volumen_agua =_parse_float_input (request .form .get ("volumen_m3","0"),None )
        if volumen_agua is None or volumen_agua <0 :
            conn .close ()
            flash ("El volumen no puede ser negativo.","error")
            return render_template ("agua_retorno_reuso.html",sede_predeterminada =sede_predeterminada )

        valor_retorno =request .form .get ("retorna_mismo_sistema_hidrico","no_informado")
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


@app .route ("/agua/registros")
@app .route ("/agua/resultados")
def agua_resultados ():
    if 'user_id'not in session :
        return redirect ("/")
    empresa =session .get ("empresa")
    periodo =request .args .get ("periodo")
    vista =request .args .get ("vista","mensual")
    conn =get_db ()
    data =_agua_calcular_resumen (conn ,empresa ,periodo )
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    if periodo :
        try :
            anio_periodo =int (str (periodo )[:4 ])
            cursor .execute (
            "SELECT id, anio, unidad, valor FROM medidas_productivas WHERE empresa = %s AND anio = %s ORDER BY id DESC LIMIT 1",
            (empresa ,anio_periodo ),
            )
        except Exception :
            cursor .execute ("SELECT id, anio, unidad, valor FROM medidas_productivas WHERE empresa = %s ORDER BY anio DESC, id DESC LIMIT 1",(empresa ,))
    else :
        cursor .execute ("SELECT id, anio, unidad, valor FROM medidas_productivas WHERE empresa = %s ORDER BY anio DESC, id DESC LIMIT 1",(empresa ,))
    medida_row =cursor .fetchone ()
    medida_valor =medida_row ["valor"]if medida_row else None 
    resultados =_agua_agrupar_resultados_reportes (data ["flujos"],data ["sedes"],data ["factores"],medida_valor =medida_valor ,vista =vista )
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
    periodo =request .args .get ("periodo")
    vista =request .args .get ("vista","mensual")
    conn =get_db ()
    data =_agua_calcular_resumen (conn ,empresa ,periodo )
    cursor =conn .cursor (cursor_factory =psycopg2 .extras .DictCursor )
    if periodo :
        try :
            anio_periodo =int (str (periodo )[:4 ])
            cursor .execute (
            "SELECT id, anio, unidad, valor, fecha_actualizacion FROM medidas_productivas WHERE empresa = %s AND anio = %s ORDER BY id DESC LIMIT 1",
            (empresa ,anio_periodo ),
            )
        except Exception :
            cursor .execute ("SELECT id, anio, unidad, valor, fecha_actualizacion FROM medidas_productivas WHERE empresa = %s ORDER BY anio DESC, id DESC LIMIT 1",(empresa ,))
    else :
        cursor .execute ("SELECT id, anio, unidad, valor, fecha_actualizacion FROM medidas_productivas WHERE empresa = %s ORDER BY anio DESC, id DESC LIMIT 1",(empresa ,))
    medida_row =cursor .fetchone ()
    medida_valor =medida_row ["valor"]if medida_row else None 
    reporte =construir_reporte_huella (empresa ,periodo or vista ,data ["flujos"],data ["sedes"],data ["factores"],medida_productiva =medida_valor ,vista =vista )
    df_resumen =pd .DataFrame (reporte ["Resumen"])
    df_flujos =pd .DataFrame (reporte ["Flujos de agua"])
    df_resultados =pd .DataFrame (reporte ["Resultados por sede"])
    df_intensidades =pd .DataFrame (reporte ["Intensidades"])
    df_factores =pd .DataFrame (reporte ["Factores de escasez aplicados"])
    df_cobertura =pd .DataFrame (reporte ["Cobertura y calidad de datos"])
    df_metodologia =pd .DataFrame (reporte ["MetodologÃ­a y supuestos"])
    sedes_incluidas =[s ["nombre_sede"]for s in data ["sedes"]]

    output =io .BytesIO ()
    with pd .ExcelWriter (output ,engine ="xlsxwriter")as writer :
        df_resumen .to_excel (writer ,index =False ,sheet_name ="Resumen")
        df_flujos .to_excel (writer ,index =False ,sheet_name ="Flujos de agua")
        df_resultados .to_excel (writer ,index =False ,sheet_name ="Resultados por sede")
        df_intensidades .to_excel (writer ,index =False ,sheet_name ="Intensidades")
        df_factores .to_excel (writer ,index =False ,sheet_name ="Factores de escasez")
        df_cobertura .to_excel (writer ,index =False ,sheet_name ="Cobertura y calidad")
        df_metodologia .to_excel (writer ,index =False ,sheet_name ="MetodologÃ­a y supuestos")
        workbook =writer .book 
        money_fmt =workbook .add_format ({"num_format":"0.000000"})
        eq_fmt =workbook .add_format ({"num_format":"0.000000"})
        text_fmt =workbook .add_format ({"text_wrap":True })
        for sheet_name in ["Resumen","Flujos de agua","Resultados por sede","Intensidades","Factores de escasez","Cobertura y calidad","MetodologÃ­a y supuestos"]:
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
            ws =writer .sheets ["MetodologÃ­a y supuestos"]
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

# ================= ELIMINACIÃ“N UNIVERSAL =================
@app .route ('/eliminar_cualquier_registro/<tipo>/<int:id>')
def eliminar_cualquier_registro (tipo ,id ):
    if 'user_id'not in session :return redirect ("/")
    empresa =session .get ('empresa')
    conn =get_db ()
    cursor =conn .cursor ()
    try :
        if tipo =='vehiculo':
            cursor .execute ("DELETE FROM combustible_movil WHERE id = %s AND empresa = %s",(id ,empresa ))
        else :
            cursor .execute ("DELETE FROM registros WHERE id = %s AND empresa = %s",(id ,empresa ))
        conn .commit ()
        flash ("Registro eliminado con Ã©xito.","success")
    except Exception as e :
        flash (f"Error al eliminar: {e }","danger")
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


    # ================= VALIDACIÃ“N DE PDFs (ADMIN) =================
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
        flash ("EnvÃ­o no encontrado.","danger")
        conn .close ()
        return redirect (url_for ('admin_pendientes'))
    try :
        filas =json .loads (pending ['datos_json']or '[]')
        empresa =pending ['empresa']
        for fila in filas :
            cursor .execute ("""
                INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,(fila ['fecha'],empresa ,
            (fila .get ('destino')or 'Operaciones')[:50 ],
            'Alcance 3','Residuos',fila ['categoria'],fila .get ('tratamiento',''),
            'kg',fila ['cantidad'],fila ['factor'],fila ['emision']))
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
    flash ("EnvÃ­o rechazado.","warning")
    return redirect (url_for ('admin_pendientes'))

    # ================= MIS ENVÃOS (USUARIO) =================
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
        return jsonify ({"error":"Token invÃ¡lido o expirado"}),401 
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
                        WHEN fuente IN ('CombustiÃ³n Fija','Combustible MÃ³vil','CombustiÃ³n Estacionaria','Refrigerantes','Fugas de Refrigerantes') THEN 'Alcance 1'
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
                    SELECT empresa, anio, unidad, valor, fecha_actualizacion
                    FROM medidas_productivas
                    WHERE empresa = ANY(%s)
                    ORDER BY empresa, anio DESC, id DESC
                """,
            (empresas_respuesta ,)
            )
            for row in cursor .fetchall ():
                empresa_row =row ["empresa"]
                total_t ,intensidad =calcular_intensidad_emisiones (conn ,empresa_row ,row ["anio"],row .get ("valor")or 0 )
                medidas_empresa_map .setdefault (empresa_row ,[]).append ({
                "anio":int (row ["anio"]or 0 ),
                "unidad":row ["unidad"],
                "valor":round (float (row ["valor"]or 0 ),6 ),
                "fecha_actualizacion":row ["fecha_actualizacion"],
                "emisiones_anio_kgco2e":round (float (total_t or 0 ),2 ),
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


            # ================= EXPORTACIÃ“N GHG GLOBAL (ADMIN) =================
@app .route ("/admin/exportar_ghg_global")
def admin_exportar_ghg_global ():
    if session .get ('es_admin')!=1 :return redirect ("/")
    anio =request .args .get ('anio',str (datetime .now ().year ))

    conn =get_db ()
    cursor =conn .cursor ()
    cursor .execute ("SELECT empresa FROM usuarios WHERE es_admin = 0 ORDER BY empresa")
    empresas =[r [0 ]for r in cursor .fetchall ()]

    filtro =" AND SUBSTRING(fecha::text,1,4) = %s"if anio !='Todos'else ""
    resumen_global =[]
    output =io .BytesIO ()

    with pd .ExcelWriter (output ,engine ='openpyxl')as writer :
        for empresa in empresas :
            p1 =(empresa ,anio )if anio !='Todos'else (empresa ,)

            _case_alc ="""
                CASE
                    WHEN fuente IN ('CombustiÃ³n Fija','Combustible MÃ³vil','CombustiÃ³n Estacionaria','Refrigerantes','Fugas de Refrigerantes') THEN 'Alcance 1'
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
            "Alcance 1 (kg COâ‚‚e)":round (total_a1 ,2 ),
            "Alcance 2 mercado (kg COâ‚‚e)":round (total_a2 ,2 ),
            "Alcance 2 ubicaciÃ³n (kg COâ‚‚e)":round (total_a2_ub ,2 ),
            "Alcance 3 (kg COâ‚‚e)":round (total_a3 ,2 ),
            "TOTAL GHG (kg COâ‚‚e)":round (total_a1 +total_a2 +total_a3 ,2 ),
            })

            if df_a1 .empty and df_a2 .empty and df_a3 .empty :
                continue 

            col_map ={
            'fecha':'Fecha','area':'Ãrea','fuente':'Fuente',
            'categoria':'CategorÃ­a','actividad':'Actividad',
            'unidad':'Unidad','cantidad':'Cantidad',
            'factor':'Factor (kg COâ‚‚/u)','emision':'EmisiÃ³n (kg COâ‚‚e)',
            'emision_ubicacion':'Emis. ubicaciÃ³n (kg COâ‚‚e)'
            }
            dfs_empresa =[]
            for alcance_name ,df_alc in [('Alcance 1',df_a1 ),('Alcance 2',df_a2 ),('Alcance 3',df_a3 )]:
                if not df_alc .empty :
                    df_copy =df_alc .copy ()
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


    # ================= EXPORTACIÃ“N GHG PROTOCOL =================
@app .route ("/exportar_ghg")
def exportar_ghg ():
    if 'user_id'not in session :return redirect ("/")
    empresa =session .get ('empresa')
    anio =request .args .get ('anio',str (datetime .now ().year ))

    conn =get_db ()

    filtro =" AND SUBSTRING(fecha::text,1,4) = %s"if anio !='Todos'else ""
    p1 =(empresa ,anio )if anio !='Todos'else (empresa ,)

    _case_alcance ="""
        CASE
            WHEN fuente IN ('CombustiÃ³n Fija','Combustible MÃ³vil','CombustiÃ³n Estacionaria','Refrigerantes','Fugas de Refrigerantes') THEN 'Alcance 1'
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

    conn .close ()

    total_a1 =float (df_a1 ['emision'].sum ())if not df_a1 .empty else 0.0 
    total_a2_mercado =float (df_a2 ['emision'].sum ())if not df_a2 .empty else 0.0 
    total_a2_ubicacion =float (df_a2 ['emision_ubicacion'].sum ())if not df_a2 .empty else 0.0 
    total_a3 =float (df_a3 ['emision'].sum ())if not df_a3 .empty else 0.0 

    df_resumen =pd .DataFrame ([
    {"Alcance":"Alcance 1 â€” Emisiones directas","Total kg COâ‚‚e":round (total_a1 ,4 )},
    {"Alcance":"Alcance 2 (mercado) â€” Electricidad comprada","Total kg COâ‚‚e":round (total_a2_mercado ,4 )},
    {"Alcance":"Alcance 2 (ubicaciÃ³n) â€” Electricidad comprada","Total kg COâ‚‚e":round (total_a2_ubicacion ,4 )},
    {"Alcance":"Alcance 3 â€” Residuos y otras indirectas","Total kg COâ‚‚e":round (total_a3 ,4 )},
    {"Alcance":"TOTAL GHG","Total kg COâ‚‚e":round (total_a1 +total_a2_mercado +total_a3 ,4 )},
    ])

    col_map ={
    'fecha':'Fecha','area':'Ãrea / Sucursal','fuente':'Fuente',
    'categoria':'CategorÃ­a / Combustible','actividad':'Actividad / Uso',
    'unidad':'Unidad','cantidad':'Cantidad','factor':'Factor (kg COâ‚‚/u)',
    'emision':'EmisiÃ³n (kg COâ‚‚e)','emision_ubicacion':'EmisiÃ³n ubicaciÃ³n (kg COâ‚‚e)',
    'origen_energia':'Origen EnergÃ­a','tiene_irec':'IREC','sistema':'Sistema ElÃ©ctrico'
    }
    for df in [df_a1 ,df_a2 ,df_a3 ]:
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
    nombre =f"GHG_Protocol_{empresa .replace (' ','_')}_{anio }_{datetime .now ().strftime ('%Y%m%d')}.xlsx"
    return send_file (output ,download_name =nombre ,as_attachment =True )


    # â”€â”€â”€ TICKETS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app .route ('/tickets')
def tickets ():
    if not session .get ('user_id'):
        return redirect ('/login')
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
        return redirect ('/login')
    empresa =session .get ('empresa')
    asunto =request .form .get ('asunto','').strip ()
    descripcion =request .form .get ('descripcion','').strip ()
    prioridad =request .form .get ('prioridad','Normal')
    if not asunto or not descripcion :
        flash ('El asunto y la descripciÃ³n son obligatorios.','error')
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
        return redirect ('/login')
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
        return redirect ('/login')
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


    # Inicia la DB
init_db ()

if __name__ =="__main__":
    app .run (host ="0.0.0.0",port =5000 ,debug =True )





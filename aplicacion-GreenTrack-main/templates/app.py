from flask import Flask, render_template, request, redirect, flash, session, send_file, jsonify, url_for
import psycopg2
import psycopg2.extras
from datetime import datetime
import pandas as pd
import hashlib
import os
import io
import csv
import os
import tempfile
from extractor_sinader import extract_sinader_data 
from extractor_sidrep import extract_sidrep_data

app = Flask(__name__)
app.secret_key = "clave_segura_provisional"

# Configuración de Base de Datos para Producción (Railway) o Local
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:sXdXXgDvrlfIFiHGgBAhGUPrqiLYCvOB@caboose.proxy.rlwy.net:14774/railway")

def get_db():
    return psycopg2.connect(DATABASE_URL)

# Filtro para números con puntos y comas
@app.template_filter('formato_cl')
def formato_cl(value):
    try:
        return "{:,.2f}".format(float(value)).replace(',', 'X').replace('.', ',').replace('X', '.')
    except:
        return value

# Filtro para mostrar Mes y Año
@app.template_filter('mes_anio')
def mes_anio(fecha_str):
    try:
        meses = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        fecha_obj = datetime.strptime(fecha_str[:10], "%Y-%m-%d")
        return f"{meses[fecha_obj.month]} {fecha_obj.year}"
    except:
        return fecha_str

# Factores de emisión base
FACTORES = {
    "Electricidad": {"kWh": 0.233},
    "Transporte": {"km": 0.192},
    "Gas": {"m3": 2.0},
    "Combustible móvil": {"L": 2.68},
    "Residuos": {"kg": 0.45},
    "Agua": {"m3": 0.34}
}

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Tabla usuarios (AQUÍ ESTÁ LA CORRECCIÓN: agregamos UNIQUE a empresa)
    cursor.execute("""
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
        fecha_registro TEXT
    )
    """)
    # Tabla registros
    cursor.execute('''
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

    cursor.execute("""
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

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS energeticos_empresa (
        id SERIAL PRIMARY KEY,
        empresa TEXT,
        energetico TEXT,
        proveedor TEXT,
        documento TEXT,
        FOREIGN KEY (empresa) REFERENCES usuarios(empresa)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS factores (
        categoria TEXT,
        unidad TEXT,
        factor REAL,
        PRIMARY KEY (categoria, unidad)
    )
    """)

    # Tablas Módulos
    cursor.execute("CREATE TABLE IF NOT EXISTS agua_consumo (id SERIAL PRIMARY KEY, empresa TEXT, fecha TEXT, agua_embotellada_litros REAL, hielo_comprado_kg REAL, hielo_producido_kg REAL, tiene_tratamiento INTEGER DEFAULT 0, descripcion_tratamiento TEXT, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor.execute("CREATE TABLE IF NOT EXISTS agua_afluentes (id SERIAL PRIMARY KEY, empresa TEXT, fecha TEXT, tipo TEXT, caudal_m3 REAL, tratamiento TEXT, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor.execute("CREATE TABLE IF NOT EXISTS agua_cuencas (id SERIAL PRIMARY KEY, empresa TEXT, fecha TEXT, tipo_cuenca TEXT, cantidad_m3 REAL, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor.execute("CREATE TABLE IF NOT EXISTS residuos_registros (id SERIAL PRIMARY KEY, empresa TEXT, fecha TEXT, periodo TEXT, tipo_residuo TEXT, cantidad_ton REAL, tratamiento TEXT, costo REAL, destino TEXT, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor.execute("CREATE TABLE IF NOT EXISTS vehiculos (id SERIAL PRIMARY KEY, empresa TEXT, patente TEXT NOT NULL, tipo TEXT, marca TEXT, modelo TEXT, anio INTEGER, estado INTEGER DEFAULT 1, fecha_registro TEXT, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor.execute("CREATE TABLE IF NOT EXISTS combustible_movil (id SERIAL PRIMARY KEY, empresa TEXT, vehiculo_id INTEGER, periodo TEXT, combustible TEXT, cantidad REAL, unidad TEXT, costo REAL, fecha_registro TEXT, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    cursor.execute("CREATE TABLE IF NOT EXISTS agua_costos (id SERIAL PRIMARY KEY, empresa TEXT, fecha TEXT, concepto TEXT, cantidad REAL, unidad TEXT, costo_usd REAL, costo_clp REAL, FOREIGN KEY (empresa) REFERENCES usuarios(empresa))")
    
    # Tabla factores_electricos
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS factores_electricos (
        anio INTEGER,
        mes INTEGER,
        sistema TEXT,
        factor_emision_avg REAL
    )
    ''')

    # Admin por defecto
    admin_password = hashlib.sha256("admin123".encode()).hexdigest()
    cursor.execute("""
        INSERT INTO usuarios 
        (empresa, email, password, contacto, es_admin, fecha_registro)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (email) DO NOTHING
    """, ("Administrador", "admin@huella.com", admin_password, "Administrador", 1, datetime.now().strftime("%Y-%m-%d %H:%M")))

    conn.commit()
    conn.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    return hash_password(password) == hashed

# ================= RUTAS AUTENTICACIÓN =================
@app.route("/", methods=["GET", "POST"])
def inicio():
    if 'user_id' in session:
        return redirect("/admin/dashboard" if session.get('es_admin') == 1 else "/dashboard")
    
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, empresa, password, es_admin FROM usuarios WHERE email = %s", (email,))
        user = cursor.fetchone()
        conn.close()
        
        if user and verify_password(password, user[2]):
            session['user_id'] = user[0]
            session['empresa'] = user[1]
            session['es_admin'] = user[3]
            return redirect("/admin/dashboard" if user[3] == 1 else "/dashboard")
        else:
            flash("Credenciales incorrectas", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada", "info")
    return redirect("/")

# ================= DASHBOARDS =================
@app.route("/dashboard")
def dashboard():
    if 'user_id' not in session or session.get('es_admin') == 1: return redirect("/")
    empresa = session.get('empresa')
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. BUSCADOR DE AÑOS
    cursor.execute("""
        SELECT DISTINCT SUBSTRING(fecha, 1, 4) FROM registros WHERE empresa = %s AND fecha IS NOT NULL
        UNION
        SELECT DISTINCT SUBSTRING(fecha_registro, 1, 4) FROM combustible_movil WHERE empresa = %s AND fecha_registro IS NOT NULL
        ORDER BY 1 DESC
    """, (empresa, empresa))
    anios_disponibles = [row[0] for row in cursor.fetchall() if row[0]]

    anio_seleccionado = request.args.get('anio', 'Todos')

    params_reg = [empresa]
    params_comb = [empresa]
    filtro_fecha_reg = ""
    filtro_fecha_comb = ""

    if anio_seleccionado != 'Todos':
        filtro_fecha_reg = " AND SUBSTRING(fecha, 1, 4) = %s"
        filtro_fecha_comb = " AND SUBSTRING(fecha_registro, 1, 4) = %s"
        params_reg.append(anio_seleccionado)
        params_comb.append(anio_seleccionado)

    # 2. TOTAL EMISIONES MANUALES
    cursor.execute(f"SELECT SUM(emision) FROM registros WHERE empresa = %s {filtro_fecha_reg}", params_reg)
    res_reg = cursor.fetchone()
    total_reg = res_reg[0] if res_reg and res_reg[0] else 0
    
    # 3. TOTAL EMISIONES DE VEHÍCULOS
    cursor.execute(f"""
        SELECT cm.cantidad, cm.combustible, cm.unidad 
        FROM combustible_movil cm WHERE cm.empresa = %s {filtro_fecha_comb}
    """, params_comb)
    comb_data = cursor.fetchall()
    
    total_comb = 0
    factores_v = {"diesel": 2.68, "bencina": 2.31, "glp": 1.51, "gnv": 2.0}
    for row in comb_data:
        cantidad, combustible, unidad = row
        f = factores_v.get(str(combustible).lower(), 2.5)
        total_comb += (cantidad or 0) * f
        
    # 4. GRÁFICO CIRCULAR
    cursor.execute(f"SELECT fuente, SUM(emision) FROM registros WHERE empresa = %s {filtro_fecha_reg} GROUP BY fuente", params_reg)
    categorias = cursor.fetchall()
    
    # 5. ÚLTIMOS REGISTROS
    params_union = params_reg + params_comb
    cursor.execute(f"""
        SELECT fecha, fuente, actividad, cantidad, unidad, emision FROM registros WHERE empresa = %s {filtro_fecha_reg}
        UNION ALL
        SELECT fecha_registro, 'Vehículos', 'Consumo ' || combustible, cantidad, unidad, (cantidad * 2.5) FROM combustible_movil WHERE empresa = %s {filtro_fecha_comb}
        ORDER BY fecha DESC LIMIT 5
    """, params_union)
    ultimos = cursor.fetchall()
    
    conn.close()
    
    return render_template("dashboard.html", 
                           total_emision=total_reg + total_comb, 
                           total_emision_registros=total_reg, 
                           total_emision_combustible=total_comb, 
                           categorias_data=categorias, 
                           ultimos_registros=ultimos,
                           empresa=empresa,
                           anios_disponibles=anios_disponibles, 
                           anio_seleccionado=anio_seleccionado)

@app.route("/combustion")
def combustion_dashboard():
    if 'user_id' not in session: return redirect("/")
    
    empresa = session.get('empresa')
    conn = get_db()
    
    # 1. El traductor DictCursor para que el HTML lea los nombres de las columnas
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # 2. Arreglamos el nombre: Ahora busca "Estacionaria" (como en tu HTML) y "Fija" por si acaso
    fuentes_comb = ('Combustión Estacionaria', 'Combustión Fija', 'Combustión Móvil')
    
    cursor.execute("""
        SELECT fecha, fuente, categoria, actividad, cantidad, unidad, emision 
        FROM registros 
        WHERE empresa = %s AND fuente IN %s 
        ORDER BY fecha DESC LIMIT 10
    """, (empresa, fuentes_comb))
    registros_comb = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("""
        SELECT categoria, SUM(emision) as total 
        FROM registros 
        WHERE empresa = %s AND fuente IN %s 
        GROUP BY categoria
    """, (empresa, fuentes_comb))
    # Para los gráficos, a veces es mejor enviar listas puras, pero los diccionarios son más seguros
    grafico_data = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("""
        SELECT SUM(emision) 
        FROM registros 
        WHERE empresa = %s AND fuente IN %s
    """, (empresa, fuentes_comb))
    res = cursor.fetchone()
    total_emision = res[0] if res and res[0] else 0
    
    conn.close()
    
    return render_template("combustion_dashboard.html", 
                           registros=registros_comb, 
                           grafico_data=grafico_data, 
                           total_emision=total_emision)

@app.route("/electricidad")
def electricidad_dashboard():
    if 'user_id' not in session: return redirect("/")
    empresa = session.get('empresa')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT fecha, categoria, actividad, cantidad, unidad, emision FROM registros WHERE empresa = %s AND fuente = 'Electricidad' ORDER BY fecha DESC LIMIT 10", (empresa,))
    registros_elec = cursor.fetchall()
    cursor.execute("SELECT actividad, SUM(emision) FROM registros WHERE empresa = %s AND fuente = 'Electricidad' GROUP BY actividad", (empresa,))
    grafico_data = cursor.fetchall()
    cursor.execute("SELECT SUM(emision) FROM registros WHERE empresa = %s AND fuente = 'Electricidad'", (empresa,))
    res = cursor.fetchone()
    total_emision = res[0] if res and res[0] else 0
    conn.close()
    return render_template("electricidad_dashboard.html", registros=registros_elec, grafico_data=grafico_data, total_emision=total_emision)

@app.route("/agua")
def agua_dashboard():
    if 'user_id' not in session: return redirect("/")
    empresa = session.get('empresa')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT fecha, agua_embotellada_litros, hielo_comprado_kg, hielo_producido_kg FROM agua_consumo WHERE empresa = %s ORDER BY fecha DESC LIMIT 5", (empresa,))
    consumo_data = cursor.fetchall()
    cursor.execute("SELECT tipo_cuenca, SUM(cantidad_m3) FROM agua_cuencas WHERE empresa = %s GROUP BY tipo_cuenca", (empresa,))
    cuencas_data = cursor.fetchall()
    cursor.execute("SELECT SUM(agua_embotellada_litros * 0.25 + hielo_comprado_kg * 0.18 + hielo_producido_kg * 0.15) FROM agua_consumo WHERE empresa = %s", (empresa,))
    res = cursor.fetchone()
    total_emision = res[0] if res and res[0] else 0
    cursor.execute("SELECT SUM(costo_usd), SUM(costo_clp) FROM agua_costos WHERE empresa = %s", (empresa,))
    costos = cursor.fetchone()
    conn.close()
    return render_template("agua_dashboard.html", empresa=empresa, consumo_data=consumo_data, cuencas_data=cuencas_data, total_emision=total_emision, total_usd=costos[0] if costos and costos[0] else 0, total_clp=costos[1] if costos and costos[1] else 0)

@app.route("/formulario_residuos", methods=['GET', 'POST'])
def formulario_residuos():
    if 'user_id' not in session: return redirect("/")
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    if request.method == "POST":
        tipo_ingreso = request.form.get("tipo_ingreso", "manual")
        empresa = session.get('empresa')
        
        # ====================================================
        # LÓGICA AUTOMÁTICA (SINADER O SIDREP)
        # ====================================================
        if tipo_ingreso in ['sinader', 'sidrep']:
            archivo_pdf = request.files.get("archivo_pdf")
            if not archivo_pdf or archivo_pdf.filename == '':
                flash("Debes subir un archivo PDF válido.", "danger")
                return redirect(request.referrer)

            try:
                # 1. Guardar temporalmente
                temp_path = os.path.join(tempfile.gettempdir(), archivo_pdf.filename)
                archivo_pdf.save(temp_path)

                # 2. Elegir el motor de extracción según la selección
                if tipo_ingreso == 'sinader':
                    df_extraido = extract_sinader_data(temp_path)
                    col_res, col_trat, col_cant = 'Residuo', 'Tipo Tratamiento', 'Cantidad (kg)'
                    col_dest = 'Destino'
                else:
                    df_extraido = extract_sidrep_data(temp_path)
                    col_res, col_trat, col_cant = 'Descripción Residuo', 'Estado del Residuo', 'Cantidad (Kg)'
                    col_dest = 'Empresa destinataria'
                
                os.remove(temp_path)

                # 3. Procesar datos unificados
                filas_guardadas = 0
                for index, row in df_extraido.iterrows():
                    residuo = str(row.get(col_res, 'Desconocido'))
                    tratamiento = str(row.get(col_trat, 'No especificado'))
                    destino = str(row.get(col_dest, ''))
                    
                    # Limpieza matemática
                    raw_cant = str(row.get(col_cant, '0')).replace('.', '').replace(',', '.')
                    try:
                        cantidad = float(raw_cant)
                    except:
                        cantidad = 0.0

                    if cantidad <= 0:
                        continue

                    # Buscar el factor en PostgreSQL
                    cursor.execute("SELECT factor FROM factores WHERE categoria ILIKE %s LIMIT 1", (f"%{residuo[:15]}%",))
                    res_factor = cursor.fetchone()
                    factor = res_factor['factor'] if res_factor else 0.0
                    
                    emision = cantidad * factor
                    fecha_bd = "2024-01-01" 

                    cursor.execute("""
                        INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (fecha_bd, empresa, destino[:50] if destino else 'Operaciones', 'Alcance 3', 'Residuos', residuo, tratamiento, 'kg', cantidad, factor, emision))
                    
                    filas_guardadas += 1

                flash(f"¡Magia pura! Se procesó el reporte {tipo_ingreso.upper()} y se calcularon {filas_guardadas} registros automáticamente.", "success")

            except Exception as e:
                flash(f"Error procesando el PDF de {tipo_ingreso.upper()}. Detalle: {str(e)}", "danger")

        # ====================================================
        # LÓGICA MANUAL (Tabla)
        # ====================================================
        else:
            periodos = request.form.getlist("periodo[]")
            tipos = request.form.getlist("tipo_residuo[]")
            cantidades = request.form.getlist("cantidad[]")
            tratamientos = request.form.getlist("tratamiento[]")
            factores_filas = request.form.getlist("factor[]")
            destinos = request.form.getlist("destino[]")
            
            filas_guardadas = 0
            for i in range(len(periodos)):
                if not periodos[i].strip() or not cantidades[i].strip():
                    continue
                
                fecha_limpia = f"{periodos[i]}-01"
                
                try:
                    cant = float(cantidades[i].replace(',', '.'))
                    fac = float(factores_filas[i].replace(',', '.'))
                except:
                    cant = 0.0
                    fac = 0.0
                    
                emision = cant * fac
                destino = destinos[i] if i < len(destinos) else ''
                
                cursor.execute("""
                    INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (fecha_limpia, empresa, destino[:50] if destino else 'Operaciones', 'Alcance 3', 'Residuos', tipos[i], tratamientos[i], 'kg', cant, fac, emision))
                filas_guardadas += 1
                
            flash(f"Se han guardado {filas_guardadas} registro(s) manuales de residuos exitosamente.", "success")
            
        conn.commit()
        conn.close()
        return redirect("/residuos")
    
    # --- Parte visual (GET) ---
    cursor.execute("SELECT categoria, unidad, factor FROM factores")
    todos_factores = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    factores_residuos = []
    palabras_clave = ['residuo', 'papel', 'cartón', 'carton', 'plástico', 'plastico', 'vidrio', 'metal', 'orgánico', 'organico']
    
    for f in todos_factores:
        cat_lower = f['categoria'].lower()
        if any(palabra in cat_lower for palabra in palabras_clave):
            factores_residuos.append(f)
    
    return render_template("formulario_residuos.html", factores=factores_residuos)

@app.route('/residuos', methods=['GET', 'POST'])
def residuos_dashboard():
    if 'user_id' not in session: return redirect("/")
    empresa = session.get('empresa')
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute("SELECT SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Residuos'", (empresa,))
    res = cursor.fetchone()
    total = res['total'] if res and res['total'] else 0.0
    
    cursor.execute("SELECT categoria, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Residuos' GROUP BY categoria", (empresa,))
    datos_cat = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("SELECT SUBSTRING(fecha, 1, 7) as mes, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Residuos' GROUP BY mes ORDER BY mes", (empresa,))
    datos_mes = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    return render_template("residuos.html", total_emision=total, datos_categoria=datos_cat, datos_mes=datos_mes)

@app.route("/refrigerantes")
def refrigerantes_dashboard():
    if 'user_id' not in session: return redirect("/")
    empresa = session.get('empresa')
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute("SELECT SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Refrigerantes'", (empresa,))
    res = cursor.fetchone()
    total_refrigerantes = res['total'] if res and res['total'] else 0.0
    
    cursor.execute("SELECT categoria, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Refrigerantes' GROUP BY categoria", (empresa,))
    datos_gases = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("SELECT SUBSTRING(fecha, 1, 7) as mes, SUM(emision) as total FROM registros WHERE empresa = %s AND fuente = 'Refrigerantes' GROUP BY mes ORDER BY mes", (empresa,))
    datos_meses = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("SELECT fecha, categoria, cantidad, unidad, emision FROM registros WHERE empresa = %s AND fuente = 'Refrigerantes' ORDER BY fecha DESC LIMIT 10", (empresa,))
    ultimos_registros = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    return render_template("refrigerantes_dashboard.html", 
                           total_refrigerantes=total_refrigerantes, 
                           datos_gases=datos_gases, 
                           datos_meses=datos_meses,
                           registros=ultimos_registros)

# ================= REGISTRO MANUAL =================
@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if 'user_id' not in session: return redirect("/")

    if request.method == 'POST':
        empresa = session.get('empresa')
        alcance = request.form.get('alcance_oculto', 'Alcance 1')
        
        # 1. Arreglamos la fecha (El HTML envía "2024-03", Postgres necesita "2024-03-01")
        fecha_raw = request.form.get('fecha')
        fecha = f"{fecha_raw}-01" if len(fecha_raw) == 7 else fecha_raw

        area = request.form.get('area')
        fuente = request.form.get('fuente')
        
        try:
            cantidad = float(str(request.form.get('cantidad', '0')).replace(',', '.'))
        except:
            cantidad = 0.0

        conn = get_db()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        if alcance == 'Alcance 2':
            # --- NUEVO CEREBRO ELÉCTRICO ---
            origen = request.form.get('origen_energia')
            sistema = request.form.get('sistema_elec')
            tiene_irec = request.form.get('tiene_irec')
            
            # El nombre ahora es dinámico (Ej: "Electricidad SEA")
            categoria = f"Electricidad {sistema}"
            unidad = "kWh"
            actividad = "Consumo de red eléctrica"
            
            # Si tiene certificado IREC o paneles solares, la huella es CERO.
            if tiene_irec == 'Si' or sistema == 'Autogeneración':
                factor = 0.0
                
                # (Opcional) Aquí capturamos el PDF si subieron uno
                archivo = request.files.get('certificado_irec')
                if archivo and archivo.filename != '':
                    # En una fase futura, aquí guardaremos el PDF en la nube
                    pass 
            else:
                # Si no, buscamos el factor real del sistema (SEN, SEA, SEM) en la base de datos
                cursor.execute("SELECT factor FROM factores WHERE categoria LIKE %s", (f"%{sistema}%",))
                res = cursor.fetchone()
                factor = res['factor'] if res else 0.0
                
            emision = cantidad * factor

        else:
            # --- CEREBRO PARA ALCANCE 1 Y 3 ---
            categoria = request.form.get('combustible')
            unidad = request.form.get('unidad')
            actividad = "Consumo general"
            
            if categoria == 'Otros':
                categoria = request.form.get('otro_combustible')
                try:
                    factor = float(str(request.form.get('otro_factor', '0')).replace(',', '.'))
                except:
                    factor = 0.0
            else:
                try:
                    factor = float(str(request.form.get('factor_oculto', '0')).replace(',', '.'))
                except:
                    factor = 0.0
                
            emision = cantidad * factor

        # 3. Guardamos todo en la base de datos oficial
        cursor.execute("""
            INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision))
        
        conn.commit()
        conn.close()
        
        flash(f"Registro guardado exitosamente (Factor aplicado: {factor})", "success")
        return redirect("/dashboard")
            
    # --- COPIA DESDE AQUÍ HACIA ABAJO ---
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT categoria, unidad, factor FROM factores")
    factores_db = [dict(row) for row in cursor.fetchall()] # Convertimos a diccionario puro
    conn.close()
    
    nombres_principales = ['Diésel', 'Bencina/Gasolina', 'Gas Licuado Petróleo (GLP)', 'R410A']
    
    # Estructura maestra para enviar a HTML sin repeticiones
    datos_agrupados = {
        'principales': {}, 'combustibles': {}, 'refrigerantes': {}, 'otros': {}
    }

    for f in factores_db:
        cat = f['categoria']
        cat_lower = cat.lower()
        
        if 'electricidad' in cat_lower or 'kwh' in f['unidad'].lower() or 'sen' in cat_lower:
            continue
            
        grupo = 'otros'
        if cat in nombres_principales: grupo = 'principales'
        elif 'gas' in cat_lower or 'diésel' in cat_lower or 'bencina' in cat_lower or 'aceite' in cat_lower or 'petróleo' in cat_lower: grupo = 'combustibles'
        elif 'r4' in cat_lower or 'hfc' in cat_lower or 'cfc' in cat_lower: grupo = 'refrigerantes'
        
        if cat not in datos_agrupados[grupo]:
            datos_agrupados[grupo][cat] = []
        
        datos_agrupados[grupo][cat].append({'unidad': f['unidad'], 'factor': f['factor']})
            
    return render_template("registro.html", datos_factores=datos_agrupados)
           
  

# ================= CARGA MASIVA (EXCEL) =================
@app.route("/descargar_plantilla")
def descargar_plantilla():
    if 'user_id' not in session: return redirect("/")
    
    columnas = ['Fecha', 'Área', 'Alcance', 'Fuente', 'Categoría', 'Actividad', 'Unidad', 'Cantidad', 'Factor']
    df_plantilla = pd.DataFrame(columns=columnas)
    df_plantilla.loc[0] = [datetime.now().strftime("%Y-%m-%d"), 'Producción', 'Alcance 1', 'Combustión Fija', 'Diésel', 'Generador', 'L', 150.5, 2.68]
    
    conn = get_db()
    df_factores = pd.read_sql_query("SELECT categoria as \"Categoría\", unidad as \"Unidad\", factor as \"Factor Oficial\" FROM factores ORDER BY categoria", conn)
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_plantilla.to_excel(writer, sheet_name='Cargar Datos', index=False)
        if not df_factores.empty:
            df_factores.to_excel(writer, sheet_name='Catálogo Oficial', index=False)
            
        for sheet in writer.sheets.values():
            sheet.set_column('A:I', 18)

    output.seek(0)
    return send_file(output, download_name="Plantilla_EcoTrack.xlsx", as_attachment=True)

@app.route("/importar", methods=["GET", "POST"])
def importar_registros():
    if 'user_id' not in session: return redirect("/")
    empresa = session.get('empresa')
    
    if request.method == "POST":
        if 'archivo' not in request.files or request.files['archivo'].filename == '':
            flash("No se seleccionó ningún archivo", "error")
            return redirect(request.url)
            
        file = request.files['archivo']
        
        if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
            try:
                try:
                    df = pd.read_excel(file, sheet_name='Datos Centralizados')
                except ValueError:
                    flash("Error: El Excel debe contener una pestaña llamada 'Datos Centralizados'.", "error")
                    return redirect(request.url)
                
                df.columns = df.columns.str.strip()
                
                df['Cantidad'] = df['Cantidad'].astype(str).str.replace(',', '.', regex=False)
                df['Cantidad'] = pd.to_numeric(df['Cantidad'], errors='coerce')
                 
                col_factor = 'Factor emisión (kg CO₂/u)'
                if col_factor in df.columns:
                    df[col_factor] = df[col_factor].astype(str).str.replace(',', '.', regex=False)
                    df[col_factor] = pd.to_numeric(df[col_factor], errors='coerce').fillna(0.0)
                else:
                    df[col_factor] = 0.0
                    
                df = df.dropna(subset=['Cantidad'])

                meses_dict = {
                    'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
                    'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
                    'septiembre': '09', 'octubre': '10', 'noviembre': '11', 'diciembre': '12'
                }

                conn = get_db()
                cursor = conn.cursor()
                guardados = 0
                
                for index, row in df.iterrows():
                    def get_texto(columna, default=''):
                        if columna not in row: return default
                        valor = row[columna]
                        if pd.isna(valor) or str(valor).strip().lower() == 'nan' or str(valor).strip() == '':
                            return default
                        return str(valor).strip()

                    mes_texto = get_texto('Mes', 'enero').lower()
                    mes_num = meses_dict.get(mes_texto, '01')
                    anio = get_texto('Año', str(datetime.now().year)).replace('.0', '')
                    fecha_sql = f"{anio}-{mes_num}-01"
                    
                    excel_level1 = get_texto('Level 1', '')
                    excel_level2 = get_texto('Level 2', '')

                    fuente = 'Desconocida'
                    if excel_level1.lower() == 'combustibles':
                        if 'fija' in excel_level2.lower(): fuente = 'Combustión Fija'
                        elif 'móvil' in excel_level2.lower() or 'movil' in excel_level2.lower(): fuente = 'Combustible Móvil'
                        else: fuente = 'Combustión Fija'
                    elif excel_level1.lower() == 'electricidad':
                        fuente = 'Electricidad'
                    elif excel_level1.lower() == 'refrigerantes':
                        fuente = 'Refrigerantes'
                    elif excel_level1.lower() == 'residuos':
                        fuente = 'Residuos'

                    alcance = get_texto('Alcance', get_texto('Scope', 'No definido'))
                    area = get_texto('Sucursal', 'General')
                    categoria = get_texto('Tipo de Combustible', '')
                    actividad = get_texto('Tipo Unidad de Consumo', '')
                    identificador = get_texto('Identificador', '')
                    unidad = get_texto('Unidad de Medida', 'N/A')
                    
                    if fuente == 'Electricidad':
                        if categoria == '': categoria = 'Red Eléctrica'
                        if actividad == '': actividad = identificador if identificador != '' else 'Consumo General'
                    
                    if categoria == '': categoria = 'Desconocida'
                    if actividad == '': actividad = 'No especificada'
                    
                    cantidad = float(row['Cantidad'])
                    factor = float(row[col_factor])
                    emision = cantidad * factor
                    
                    cursor.execute("""
                        INSERT INTO registros (fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (fecha_sql, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision))
                    guardados += 1
                    
                conn.commit()
                conn.close()
                flash(f"¡Éxito! Se importaron {guardados} registros libres de errores.", "success")
                return redirect("/dashboard")
                
            except Exception as e:
                flash(f"Error técnico al procesar. Detalle: {str(e)}", "error")
                return redirect(request.url)
        else:
            flash("Formato no válido. Debe ser .xlsx", "error")
            return redirect(request.url)
            
    return render_template("importar.html")

# ================= EXPORTACIÓN =================
@app.route("/exportar")
def exportar():
    return exportar_avanzado()

@app.route("/exportar_completo")
def exportar_completo():
    return exportar_avanzado()

@app.route("/exportar_avanzado")
def exportar_avanzado():
    if 'user_id' not in session: return redirect("/")
    fecha_inicio = request.args.get('fecha_inicio')
    fecha_fin = request.args.get('fecha_fin')
    empresa_filtro = request.args.get('empresa') 
    
    conn = get_db()
    query = "SELECT fecha, empresa, area, alcance, fuente, categoria, actividad, unidad, cantidad, factor, emision FROM registros WHERE 1=1"
    params = []
    
    if session.get('es_admin') != 1:
        query += " AND empresa = %s"
        params.append(session.get('empresa'))
    elif empresa_filtro: 
        query += " AND empresa = %s"
        params.append(empresa_filtro)
        
    if fecha_inicio:
        query += " AND fecha >= %s"
        params.append(fecha_inicio)
    if fecha_fin:
        query += " AND fecha <= %s"
        params.append(fecha_fin + " 23:59")
        
    df = pd.read_sql_query(query, conn, params=tuple(params))
    conn.close()
    
    df.rename(columns={
        'fecha': 'Fecha', 'empresa': 'Empresa', 'area': 'Área', 'alcance': 'Alcance', 'fuente': 'Fuente',
        'categoria': 'Combustible/Categoría', 'actividad': 'Uso', 'unidad': 'Unidad', 'cantidad': 'Cantidad',
        'factor': 'Factor', 'emision': 'Emisiones (kg CO2)'
    }, inplace=True)
    
    archivo = f"Reporte_EcoTrack_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    df.to_excel(archivo, index=False)
    return send_file(archivo, as_attachment=True)

# ================= RUTAS ADMIN Y OTROS =================
def get_admin_stats():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM usuarios WHERE es_admin = 0")
    total_emp = cursor.fetchone()[0] or 0
    cursor.execute("SELECT COUNT(*) FROM registros")
    total_reg = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(emision) FROM registros")
    total_em = cursor.fetchone()[0] or 0
    conn.close()
    return total_emp, total_reg, total_em

@app.route("/mis_datos")
def mis_datos():
    if 'user_id' not in session: return redirect("/")
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT * FROM usuarios WHERE id = %s", (session['user_id'],))
    datos = cursor.fetchone()
    conn.close()
    return render_template("mis_datos.html", datos_usuario=datos)

@app.route("/admin/dashboard")
def admin_dashboard():
    if 'user_id' not in session or session.get('es_admin') != 1: return redirect("/")
    t_emp, t_reg, t_em = get_admin_stats()
    
    conn = get_db()
    # EL TRUCO: Usar DictCursor para que el HTML reconozca los nombres de las columnas
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute("SELECT empresa, email, contacto, fecha_registro FROM usuarios WHERE es_admin = 0 ORDER BY fecha_registro DESC LIMIT 5")
    # Convertimos a diccionario puro
    ultimas = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("SELECT id, empresa FROM usuarios WHERE es_admin = 0 ORDER BY empresa")
    empresas_filtro = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return render_template("admin.html", 
                           total_empresas=t_emp, total_registros=t_reg, total_emisiones=t_em, 
                           ultimas_empresas=ultimas, empresas=empresas_filtro, admin_section="dashboard")

@app.route("/admin/empresas")
def admin_empresas():
    if session.get('es_admin') != 1: return redirect("/")
    t_emp, t_reg, t_em = get_admin_stats()
    
    conn = get_db()
    # EL TRUCO: Usar DictCursor
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute("SELECT id, empresa, email, contacto, rut, fecha_registro FROM usuarios WHERE es_admin = 0 ORDER BY empresa")
    empresas = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return render_template("admin.html", empresas=empresas, admin_section="empresas", 
                           total_empresas=t_emp, total_registros=t_reg, total_emisiones=t_em)

@app.route("/admin/factores", methods=["GET", "POST"])
def admin_factores():
    if session.get('es_admin') != 1: return redirect("/")
    t_emp, t_reg, t_em = get_admin_stats()
    
    conn = get_db()
    if request.method == "POST":
        try:
            conn.cursor().execute("""
                INSERT INTO factores (categoria, unidad, factor) 
                VALUES (%s, %s, %s)
                ON CONFLICT (categoria, unidad) 
                DO UPDATE SET factor = EXCLUDED.factor
            """, (request.form.get("categoria"), request.form.get("unidad"), float(request.form.get("factor"))))
            conn.commit()
            flash("Factor guardado exitosamente", "success")
        except Exception as e: 
            flash(f"Error al guardar: {e}", "error")
            conn.rollback()
    
    df = pd.read_sql_query("SELECT * FROM factores ORDER BY categoria, unidad", conn)
    conn.close()
    
    return render_template("admin.html", factores=df.to_dict('records'), admin_section="factores", 
                           total_empresas=t_emp, total_registros=t_reg, total_emisiones=t_em)

@app.route("/admin/exportar_todo")
def admin_exportar_todo():
    return exportar_avanzado()

@app.route("/admin/crear_empresa", methods=["POST"])
def admin_crear_empresa():
    if session.get('es_admin') != 1: return redirect("/")
    
    empresa = request.form.get("empresa")
    email = request.form.get("email")
    password = hash_password(request.form.get("password"))
    contacto = request.form.get("contacto")
    rut = request.form.get("rut")

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO usuarios (empresa, email, password, contacto, rut, fecha_registro, es_admin)
            VALUES (%s, %s, %s, %s, %s, %s, 0)
        """, (empresa, email, password, contacto, rut, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        flash(f"Empresa '{empresa}' creada exitosamente.", "success")
    except psycopg2.IntegrityError:
        conn.rollback()
        flash("Error: El correo electrónico ya está en uso.", "error")
    finally:
        conn.close()
    
    return redirect(url_for('admin_empresas'))

@app.route("/admin/eliminar_factor/<categoria>/<unidad>")
def admin_eliminar_factor(categoria, unidad):
    if session.get('es_admin') != 1: return redirect("/")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM factores WHERE categoria = %s AND unidad = %s", (categoria, unidad))
    conn.commit()
    conn.close()
    flash("Factor eliminado.", "info")
    return redirect(url_for('admin_factores'))

@app.route("/admin/cargar_factores", methods=["POST"])
def cargar_factores():
    if session.get('es_admin') != 1: return redirect("/")
    if 'archivo_factores' not in request.files or request.files['archivo_factores'].filename == '':
        flash("No se subió ningún archivo", "danger")
        return redirect(request.referrer)
        
    file = request.files['archivo_factores']
    try:
        df = pd.read_excel(file, sheet_name='Equivalencias', header=None, engine='openpyxl')
        conn = get_db()
        cursor = conn.cursor()
        actualizados = 0
        
        def safe_get(row_data, idx):
            if idx < len(row_data):
                val = row_data.iloc[idx]
                return str(val).strip() if pd.notna(val) and str(val).strip() != 'nan' else None
            return None

        def safe_float(val):
            if val is None: return None
            try:
                return float(str(val).replace(',', '.'))
            except (ValueError, TypeError):
                return None

        for index, row in df.iterrows():
            if index < 3: continue 
            
            # --- SECCIÓN ACTUALIZADA A FACTORES 2024 ---
            # Si en el futuro quieres los de 2025, cambia los índices a 8, 15 y 24.
            
            # Combustibles
            cat_comb = safe_get(row, 2)
            uni_comb = safe_get(row, 5) or 'N/A'
            fe_comb = safe_float(safe_get(row, 7)) # Índice 7 = Columna 2024
            
            if cat_comb and fe_comb is not None:
                cursor.execute("""
                    INSERT INTO factores (categoria, unidad, factor) VALUES (%s, %s, %s)
                    ON CONFLICT (categoria, unidad) DO UPDATE SET factor = EXCLUDED.factor
                """, (cat_comb, uni_comb, fe_comb))
                actualizados += 1

            # Refrigerantes
            cat_ref = safe_get(row, 12)
            fe_ref = safe_float(safe_get(row, 14)) # Índice 14 = Columna 2024
            
            if cat_ref and fe_ref is not None:
                cursor.execute("""
                    INSERT INTO factores (categoria, unidad, factor) VALUES (%s, %s, %s)
                    ON CONFLICT (categoria, unidad) DO UPDATE SET factor = EXCLUDED.factor
                """, (cat_ref, 'kg', fe_ref))
                actualizados += 1

            # Residuos
            cat_res = safe_get(row, 22)
            fe_res = safe_float(safe_get(row, 23)) # Índice 23 = Columna 2024
            
            if cat_res and fe_res is not None:
                cursor.execute("""
                    INSERT INTO factores (categoria, unidad, factor) VALUES (%s, %s, %s)
                    ON CONFLICT (categoria, unidad) DO UPDATE SET factor = EXCLUDED.factor
                """, (cat_res, 'kg', fe_res))
                actualizados += 1
                
        conn.commit()
        conn.close()
        
        if actualizados > 0:
            flash(f"¡Sincronización Exitosa! Se actualizaron o agregaron {actualizados} factores desde el Excel.", "success")
        else:
            flash("El archivo se leyó, pero no se encontraron factores.", "warning")
    except Exception as e:
        flash(f"Error técnico al leer el Excel. Detalle: {str(e)}", "danger")
        
    return redirect(request.referrer)

@app.route("/admin/cargar_electricidad", methods=["POST"])
def cargar_electricidad():
    if 'user_id' not in session or session.get('es_admin') != 1: 
        return redirect("/")

    archivo = request.files.get("archivo_electricidad")
    if not archivo or archivo.filename == "":
        flash("No se seleccionó ningún archivo.", "danger")
        return redirect(request.referrer) 

    try:
        # TRAMPA 1 RESUELTA: Forzamos a Pandas a leer la pestaña correcta
        df = pd.read_excel(archivo, sheet_name='Factores eléctricos')
        
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM factores_electricos")

        for index, row in df.iterrows():
            if pd.notna(row.iloc[0]) and pd.notna(row.iloc[3]):
                try:
                    anio = int(row.iloc[0])
                    mes = int(row.iloc[1])
                    sistema = str(row.iloc[2]).strip()
                    factor = float(row.iloc[3])
                    
                    cursor.execute('''
                        INSERT INTO factores_electricos (anio, mes, sistema, factor_emision_avg)
                        VALUES (%s, %s, %s, %s)
                    ''', (anio, mes, sistema, factor))
                except ValueError:
                    continue 
                
        conn.commit()
        conn.close()
        flash("¡Base de datos eléctrica actualizada con éxito!", "success")
    except Exception as e:
        flash(f"Error técnico al leer el Excel: {str(e)}", "danger")
        
    return redirect(request.referrer)
# ================= MÓDULO VEHÍCULOS =================
@app.route("/vehiculos")
def vehiculos():
    if 'user_id' not in session: return redirect("/")
    empresa = session.get('empresa')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vehiculos WHERE empresa = %s ORDER BY patente", (empresa,))
    vehiculos_data = cursor.fetchall()
    conn.close()
    return render_template("vehiculos.html", vehiculos_data=vehiculos_data)

@app.route("/api/vehiculos", methods=["GET", "POST"])
def api_vehiculos():
    if 'user_id' not in session: return jsonify({"success": False, "message": "No autorizado"}), 401
    empresa = session.get('empresa')
    if request.method == "GET":
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, patente, tipo, marca, modelo, anio FROM vehiculos WHERE empresa = %s ORDER BY patente", (empresa,))
        vehiculos = cursor.fetchall()
        conn.close()
        vehiculos_formatted = [{"id": v[0], "patente": v[1], "tipo": v[2], "marca": v[3], "modelo": v[4], "anio": v[5]} for v in vehiculos]
        return jsonify(vehiculos_formatted)
    elif request.method == "POST":
        data = request.get_json()
        if not data.get('patente'): return jsonify({"success": False, "message": "Patente obligatoria"}), 400
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM vehiculos WHERE empresa = %s AND patente = %s", (empresa, data['patente'].upper()))
        if cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "message": "Patente ya registrada"}), 400
        cursor.execute("""
            INSERT INTO vehiculos (empresa, patente, tipo, marca, modelo, anio, fecha_registro)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id;
        """, (empresa, data['patente'].upper(), data.get('tipo'), data.get('marca'), data.get('modelo'), data.get('anio'), datetime.now().strftime("%Y-%m-%d %H:%M")))
        vehiculo_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Vehículo guardado", "vehiculo": {"id": vehiculo_id, "patente": data['patente'].upper()}})

@app.route("/vehiculos/eliminar/<int:id>")
def eliminar_vehiculo(id):
    if 'user_id' not in session: return redirect("/")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM vehiculos WHERE id = %s AND empresa = %s", (id, session.get('empresa')))
    conn.commit()
    conn.close()
    flash("Vehículo eliminado correctamente", "info")
    return redirect(url_for('vehiculos'))

@app.route("/combustible/movil/eliminar/<int:id>")
def eliminar_registro_combustible(id):
    if 'user_id' not in session: return redirect("/")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM combustible_movil WHERE id = %s AND empresa = %s", (id, session.get('empresa')))
    conn.commit()
    conn.close()
    flash("Registro de combustible eliminado", "info")
    return redirect(url_for('combustible_movil'))

@app.route("/configuracion_sistema")
def configuracion_sistema():
    return redirect(url_for('mis_datos'))

@app.route("/combustible/movil")
def combustible_movil():
    if 'user_id' not in session: return redirect("/")
    empresa = session.get('empresa')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vehiculos WHERE empresa = %s", (empresa,))
    vehiculos = cursor.fetchall()
    conn.close()
    return render_template("combustible_movil.html", vehiculos=vehiculos)

@app.route("/api/combustible/movil", methods=["POST"])
def api_combustible_movil():
    if 'user_id' not in session: return jsonify({"success": False}), 401
    empresa = session.get('empresa')
    data = request.get_json()
    conn = get_db()
    cursor = conn.cursor()
    guardados = 0
    for registro in data.get('registros', []):
        try:
            cursor.execute("""
                INSERT INTO combustible_movil (empresa, vehiculo_id, periodo, combustible, cantidad, unidad, costo, fecha_registro)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (empresa, registro['vehiculo_id'], registro['periodo'], registro['combustible'], registro['cantidad'], registro['unidad'], registro.get('costo', 0), datetime.now().strftime("%Y-%m-%d %H:%M")))
            guardados += 1
        except: pass
    conn.commit()
    conn.close()
    return jsonify({"success": True, "guardados": guardados})

@app.route("/agua/registro", methods=["GET", "POST"])
def agua_registro():
    return render_template("agua_registro.html")
@app.route("/agua/reporte")
def agua_reporte():
    return render_template("agua_reporte.html")
@app.route("/residuos/registro", methods=["GET", "POST"])
def residuos_registro():
    return render_template("residuos_registro.html")
@app.route("/residuos/reporte")
def residuos_reporte():
    return render_template("residuos_reporte.html")

@app.route("/configuracion")
def configuracion():
    if 'user_id' not in session: return redirect("/")
    return redirect(url_for('mis_datos'))

@app.route("/admin/empresa/<string:nombre_empresa>")
def admin_detalle_empresa(nombre_empresa):
    if session.get('es_admin') != 1: return redirect("/")
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cursor.execute("SELECT COUNT(*) as total_reg, SUM(emision) as total_emi FROM registros WHERE empresa = %s", (nombre_empresa,))
    kpis = cursor.fetchone()
    
    cursor.execute("SELECT fuente, SUM(emision) as total FROM registros WHERE empresa = %s GROUP BY fuente", (nombre_empresa,))
    datos_fuente = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("SELECT SUBSTRING(fecha, 1, 7) as mes, SUM(emision) as total FROM registros WHERE empresa = %s GROUP BY mes ORDER BY mes", (nombre_empresa,))
    datos_mes = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return render_template("admin_detalle.html", 
                           empresa=nombre_empresa, 
                           kpis=kpis, 
                           datos_fuente=datos_fuente, 
                           datos_mes=datos_mes)

@app.route("/admin/exportar/<string:nombre_empresa>")
def exportar_datos_empresa(nombre_empresa):
    if session.get('es_admin') != 1: return redirect("/")
    
    anio = request.args.get('anio', 'Todos')
    conn = get_db()
    
    if anio == 'Todos':
        query = "SELECT fecha, area, alcance, fuente, categoria, actividad, cantidad, unidad, factor, emision FROM registros WHERE empresa = %s ORDER BY fecha DESC"
        df = pd.read_sql_query(query, conn, params=(nombre_empresa,))
    else:
        query = "SELECT fecha, area, alcance, fuente, categoria, actividad, cantidad, unidad, factor, emision FROM registros WHERE empresa = %s AND fecha LIKE %s ORDER BY fecha DESC"
        df = pd.read_sql_query(query, conn, params=(nombre_empresa, f"{anio}-%"))
        
    conn.close()
    
    if df.empty:
        flash(f"La empresa no tiene registros para el periodo seleccionado ({anio}).", "warning")
        return redirect(request.referrer)
        
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        nombre_hoja = f'Auditoria_{anio}'
        df.to_excel(writer, sheet_name=nombre_hoja, index=False)
        worksheet = writer.sheets[nombre_hoja]
        for columna in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']:
            worksheet.column_dimensions[columna].width = 18
            
    output.seek(0)
    nombre_archivo = f"Reporte_Auditoria_{nombre_empresa.replace(' ', '_')}_{anio}.xlsx"
    return send_file(output, download_name=nombre_archivo, as_attachment=True)

@app.route("/alcance_3")
def alcance_3():
    if 'user_id' not in session:
        return redirect("/")
    # Esta ruta ahora solo sirve para redirigir al formulario de residuos
    return redirect(url_for('formulario_residuos'))
# Esta línea suelta "despierta" la base de datos en Railway
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
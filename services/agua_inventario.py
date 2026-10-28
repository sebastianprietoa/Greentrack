"""Diagnóstico, validación y resumen hídrico compartido por las vistas."""
from decimal import Decimal
import re

import psycopg2.extras
from services.combustibles_carbono import fecha_periodo, numero_finito
from services.energia_factores import calcular_suministro, resolver_factor
from services.huella_agua import (HuellaAguaError, calcular_consumo_operativo_estimado,
    generar_indicador_calidad_datos, resultados_por_periodo, validar_factor_escasez)

MENSAJES = {'sin_medicion':'No se puede obtener la huella azul: faltan mediciones de entrada y/o salida.',
            'solo_entrada':'No se puede obtener la huella azul: solo consumo de entrada; falta medición de salida.',
            'ambos':None}


def diagnosticar(entrada,salida):
    if entrada not in ('caudalimetro','boleta','no') or salida not in ('caudalimetro','descarga','no'):
        raise ValueError('Selecciona una medición válida para entrada y salida.')
    return 'sin_medicion' if entrada=='no' else 'solo_entrada' if salida=='no' else 'ambos'


def leer_diagnostico(cursor,empresa):
    cursor.execute('SELECT medicion_entrada_agua,medicion_salida_agua FROM configuracion WHERE empresa=%s',(empresa,))
    row = cursor.fetchone()
    try:
        estado = diagnosticar(row[0],row[1]) if row else None
    except ValueError:
        estado = None
    return estado, MENSAJES.get(estado,MENSAJES['sin_medicion'])


def validar_nuevo_flujo(cursor,empresa,sede_id,periodo,tipo,volumen):
    volumen = numero_finito(volumen,'El volumen de agua')
    cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('agua:'+empresa,))
    estado,mensaje = leer_diagnostico(cursor,empresa)
    if estado not in ('ambos','solo_entrada'):
        raise ValueError(mensaje)
    if tipo not in ('captacion','retorno','reuso'):
        raise ValueError('El tipo de flujo no es válido.')
    if estado=='solo_entrada' and tipo!='captacion':
        raise ValueError(MENSAJES['solo_entrada'])
    if sede_id is not None:
        cursor.execute('SELECT 1 FROM agua_sedes WHERE id=%s AND empresa=%s AND activo=TRUE',(sede_id,empresa))
        if not cursor.fetchone():
            raise ValueError('La sede no existe, está inactiva o pertenece a otra empresa.')
    cursor.execute('''SELECT tipo_flujo,volumen_m3,retorna_mismo_sistema_hidrico FROM agua_flujos
        WHERE empresa=%s AND sede_id IS NOT DISTINCT FROM %s AND to_char(periodo,'YYYY-MM')=%s''',(empresa,sede_id,str(periodo)[:7]))
    captacion = Decimal(str(volumen)) if tipo=='captacion' else Decimal(0)
    retorno = Decimal(str(volumen)) if tipo=='retorno' else Decimal(0)
    reuso = Decimal(str(volumen)) if tipo=='reuso' else Decimal(0)
    for row in cursor.fetchall():
        valor = Decimal(str(numero_finito(row[1],'El volumen registrado')))
        if row[0]=='captacion': captacion += valor
        elif row[0]=='retorno': retorno += valor
        elif row[0]=='reuso': reuso += valor
    calcular_consumo_operativo_estimado(captacion,retorno,reuso)
    return volumen


def normalizar_periodo(periodo):
    if not periodo:
        return None
    if re.fullmatch(r'\d{4}',str(periodo)) and 1 <= int(periodo) <= 9999:
        return str(periodo)
    fecha_periodo(periodo)
    return periodo


def crear_resumen_agua(connection,empresa,periodo=None):
    periodo = normalizar_periodo(periodo)
    cursor = connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
    estado,mensaje = leer_diagnostico(cursor,empresa)
    params = [empresa]
    filtro = ''
    if periodo:
        filtro = ' AND SUBSTRING(periodo::text,1,%s)=%s'
        params.extend([len(periodo),periodo])
    cursor.execute('SELECT * FROM agua_flujos WHERE empresa=%s'+filtro+' ORDER BY periodo,id',params)
    flujos = [dict(r) for r in cursor.fetchall()]
    cursor.execute('SELECT * FROM agua_sedes WHERE empresa=%s ORDER BY nombre_sede',(empresa,))
    sedes = [dict(r) for r in cursor.fetchall()]
    cursor.execute('SELECT * FROM factores_escasez_agua WHERE activo=TRUE ORDER BY fecha_carga DESC,id DESC')
    factores = []
    for row in cursor.fetchall():
        try:
            validar_factor_escasez(dict(row))
            factores.append(dict(row))
        except HuellaAguaError:
            continue
    cursor.execute('SELECT anio,unidad,valor FROM medidas_productivas WHERE empresa=%s',(empresa,))
    medidas = {int(r[0]):{'unidad':r[1],'valor':float(r[2] or 0)} for r in cursor.fetchall()}
    params_suministro = [empresa,'%combusti%']
    filtro_suministro = ''
    if periodo:
        filtro_suministro = ' AND SUBSTRING(r.fecha::text,1,%s)=%s'
        params_suministro.extend([len(periodo),periodo])
    cursor.execute('''SELECT r.id,r.fecha,r.fuente AS fuente_consumo,r.categoria AS categoria_consumo,
        r.cantidad,r.unidad,r.origen_energia,r.sistema,r.nivel_tension,h.huella_suministro_m3,h.warning
        FROM registros r LEFT JOIN resultados_huella_suministro_agua h ON h.registro_id=r.id AND h.empresa=r.empresa
        WHERE r.empresa=%s AND (r.fuente='Electricidad' OR LOWER(r.fuente) LIKE %s)'''+filtro_suministro,params_suministro)
    suministro = [dict(r) for r in cursor.fetchall()]
    faltantes_suministro = []
    for row in suministro:
        if row['huella_suministro_m3'] is None:
            factor_suministro = resolver_factor(row['fuente_consumo'],row['categoria_consumo'],row['unidad'],
                row['origen_energia'],row['sistema'],row['nivel_tension'])
            calculo = calcular_suministro(row['cantidad'],row['unidad'],factor_suministro)
            row['huella_suministro_m3'] = calculo['water_m3']
            if calculo['water_m3'] is None:
                motivo = {'requires_waste_model':'Este combustible requiere un factor hídrico específico para residuos.',
                    'requires_blend':'Falta la composición de la mezcla para aplicar un factor hídrico.',
                    'not_applicable':'No hay un factor hídrico compatible con este consumo.'}.get(factor_suministro.get('water_status'))
                faltantes_suministro.append({'id':row['id'],'fecha':str(row['fecha']),
                    'fuente':row['fuente_consumo'],'categoria':row['categoria_consumo'],
                    'motivo':motivo or calculo['warning'] or row['warning'] or 'Falta un factor hídrico compatible.'})
    rows = resultados_por_periodo(flujos,sedes,factores,{y:m['valor'] for y,m in medidas.items()})
    def total(campo,grupo=rows):
        return sum(float(r[campo]) for r in grupo) if all(r[campo] is not None for r in grupo) else None
    completo = bool(rows) and all(r['medicion_completa'] for r in rows) and estado=='ambos'
    consumo = total('consumo_operativo_m3') if completo else None
    errores = [f"{r['nombre_sede']} / {r['periodo']}: {r['error_balance']}" for r in rows if r.get('error_balance')]
    suministro_total = sum(float(r['huella_suministro_m3'] or 0) for r in suministro)
    suministro_completo = all(r['huella_suministro_m3'] is not None for r in suministro)
    suministro_disponible = any(r['huella_suministro_m3'] is not None for r in suministro)
    huella_total = consumo+suministro_total if consumo is not None and suministro_completo else None
    huella = total('huella_escasez_m3eq') if completo else None
    if estado=='ambos':
        mensaje = 'Balance de agua inválido. Revisa los registros.' if errores else 'No se puede obtener la huella azul: faltan registros de entrada y/o salida.' if not completo else None
    else:
        mensaje = MENSAJES.get(estado, MENSAJES['sin_medicion'])
    meses = {str(f['periodo'])[:7] for f in flujos} | {str(r['fecha'])[:7] for r in suministro}
    meses_completos = {mes for mes in meses if estado=='ambos' and any(r['periodo']==mes for r in rows)
                       and all(r['medicion_completa'] for r in rows if r['periodo']==mes)}
    tendencia,por_fuente,por_sede = {},{},{}
    sede_map = {s['id']:s for s in sedes}
    for flujo in flujos:
        mes = str(flujo['periodo'])[:7]
        tendencia.setdefault(mes,{'captacion':0,'retorno':0,'reuso':0})
        nombre = sede_map.get(flujo['sede_id'],{}).get('nombre_sede','Empresa')
        por_sede.setdefault(nombre,{'captacion':0,'retorno':0,'reuso':0})
        tipo = flujo['tipo_flujo']
        try:
            valor = numero_finito(flujo['volumen_m3'],'El volumen registrado')
        except ValueError:
            continue
        if tipo=='captacion':
            fuente = flujo.get('fuente_agua') or 'Sin fuente'
            por_fuente[fuente] = por_fuente.get(fuente,0)+valor
        if tipo in ('captacion','reuso') or (tipo=='retorno' and flujo.get('retorna_mismo_sistema_hidrico') is True):
            tendencia[mes][tipo] += valor
            por_sede[nombre][tipo] += valor
    for mes,datos_mes in tendencia.items():
        grupo = [r for r in rows if r['periodo']==mes]
        datos_mes['consumo'] = total('consumo_operativo_m3',grupo) if grupo and estado=='ambos' and all(r['medicion_completa'] for r in grupo) else None
    suministro_por_fuente = {}
    for row in suministro:
        if row['huella_suministro_m3'] is None:
            continue
        key = f"{row['fuente_consumo']} - {row['categoria_consumo']}"
        suministro_por_fuente[key] = suministro_por_fuente.get(key,0)+float(row['huella_suministro_m3'] or 0)
    cobertura = []
    sedes_visibles = list(sedes)
    if any(r['sede_id'] is None for r in rows) or not sedes:
        sedes_visibles.append({'id':None,'nombre_sede':'Empresa'})
    for sede in sedes_visibles:
        grupo = [r for r in rows if r['sede_id']==sede['id']]
        valida = bool(grupo) and all(r['medicion_completa'] for r in grupo) and estado=='ambos'
        caracterizada = valida and all(r['huella_escasez_m3eq'] is not None for r in grupo)
        cobertura.append({'sede':sede,'periodo':periodo or 'Histórico','captacion':total('captacion_m3',grupo),
            'retorno':total('retorno_mismo_sistema_m3',grupo),'reuso':total('reuso_m3',grupo),
            'ubicacion':'Completa' if all(sede.get(k) for k in ('codigo_cuenca','nombre_cuenca','region','comuna')) else 'Parcial' if sede.get('region') or sede.get('comuna') else 'Pendiente',
            'factor_disponible':bool(grupo) and all(r['factor'] is not None for r in grupo),
            'resultado':'Balance inválido' if any(r.get('error_balance') for r in grupo) else 'Huella de escasez calculada' if caracterizada else 'Solo inventario físico disponible' if valida else 'Datos insuficientes',
            'huella_escasez':total('huella_escasez_m3eq',grupo) if caracterizada else None})
    years = {int(mes[:4]) for mes in meses}
    denominador = None
    if not periodo or len(periodo)==4:
        if years and all(y in medidas and medidas[y]['valor']>0 for y in years) and len({medidas[y]['unidad'] for y in years})==1:
            denominador = sum(medidas[y]['valor'] for y in years)
    return {'sedes':sedes,'flujos':flujos,'factores':factores,'medidas_por_anio':{y:m['valor'] for y,m in medidas.items()},
        'captacion_total':total('captacion_m3'),'retornos_totales':total('retorno_m3'),
        'retorno_mismo':total('retorno_mismo_sistema_m3'),'reuso':total('reuso_m3'),
        'consumo':consumo,'factor':rows[0]['factor'] if len(rows)==1 else None,'huella':huella,
        'huella_hidrica_total':huella_total,'huella_suministro_total':suministro_total,
        'nivel':'Huella de escasez calculada' if huella is not None else 'Solo inventario físico disponible' if completo else 'Datos insuficientes',
        'calidad':generar_indicador_calidad_datos(flujos) if flujos else 'baja',
        'intensidad_hidrica':consumo/denominador if consumo is not None and denominador else None,
        'intensidad_escasez':huella/denominador if huella is not None and denominador else None,
        'intensidad_hidrica_total':huella_total/denominador if huella_total is not None and denominador else None,
        'tendencia':tendencia,'por_fuente':por_fuente,'por_sede':por_sede,'suministro_por_fuente':suministro_por_fuente,
        'cobertura':cobertura,'total_sedes':len(sedes_visibles),'sedes_con_datos':len({f['sede_id'] for f in flujos}),
        'periodos_con_datos':len(meses),'periodos_inventario_completo':len(meses_completos),
        'estado_medicion_agua':estado,'mensaje_medicion_agua':mensaje,'errores_balance':errores,
        'puede_medir_huella':huella_total is not None,'suministro_completo':suministro_completo,
        'suministro_disponible':suministro_disponible,'consumos_energeticos':len(suministro),
        'faltantes_suministro':faltantes_suministro}

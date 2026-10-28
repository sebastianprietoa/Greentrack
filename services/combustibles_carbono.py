"""Factores de carbono por combustible, unidad y año, desde el catálogo existente."""
import math
import re
import unicodedata
from datetime import datetime


def normalizar(valor):
    texto = unicodedata.normalize('NFKD', str(valor or '')).encode('ascii','ignore').decode()
    return ' '.join(texto.lower().replace('_',' ').split())


def numero_finito(valor, nombre, positivo=False):
    try:
        resultado = float(str(valor).replace(',','.'))
    except (ValueError, TypeError):
        raise ValueError(f'{nombre} debe ser numérico.') from None
    if not math.isfinite(resultado) or resultado < 0 or (positivo and resultado == 0):
        raise ValueError(f'{nombre} debe ser finito y {"mayor a cero" if positivo else "no negativo"}.')
    return resultado


def fecha_periodo(periodo):
    if not re.fullmatch(r'\d{4}-\d{2}',str(periodo or '')):
        raise ValueError('El período debe tener formato año-mes.')
    try:
        return datetime.strptime(periodo, '%Y-%m').date()
    except ValueError:
        raise ValueError('El período no es válido.') from None


def unidad_canonica(unidad):
    valor = normalizar(unidad)
    for canonica, aliases in {
        'l':('l','lt','lts','litro','litros'),
        'kg':('kg','kilogramo','kilogramos'),
        't':('t','ton','tonelada','toneladas'),
        'm3':('m3','metro cubico','metros cubicos'),
        'kwh':('kwh',), 'mwh':('mwh',),
    }.items():
        if valor in aliases:
            return canonica
    raise ValueError('Unidad de combustible no admitida.')


def resolver_factor_carbono(cursor, combustible, unidad, anio):
    aliases = {
        'diesel':'Diésel fósil puro',
        'bencina':'Bencina/Gasolina',
        'gasolina':'Bencina/Gasolina',
        'glp':'GLP', 'gas lp':'GLP',
        'gas natural':'Gas Natural', 'carbon':'Carbón',
    }
    categoria = aliases.get(normalizar(combustible))
    if not categoria:
        raise ValueError('Selecciona un combustible específico del catálogo en Nuevo Registro; no se puede aplicar un factor genérico.')
    return categoria, resolver_factor_catalogo(cursor,categoria,unidad,anio)


def resolver_factor_catalogo(cursor,categoria,unidad,anio):
    unidad_norm = unidad_canonica(unidad)
    cursor.execute('SELECT categoria, unidad, factor, COALESCE(anio,0) AS anio FROM factores WHERE categoria=%s', (categoria,))
    candidatos = []
    for row in cursor.fetchall():
        try:
            unidad_fila = unidad_canonica(row[1])
        except ValueError:
            continue
        if unidad_fila != unidad_norm or int(row[3]) not in (0,anio):
            continue
        factor = numero_finito(row[2], 'El factor de emisión', positivo=True)
        candidatos.append((int(row[3]),factor))
    elegibles = [factor for year,factor in candidatos if year == anio]
    if not elegibles:
        elegibles = [factor for year,factor in candidatos if year == 0]
    if len(elegibles) != 1:
        raise ValueError('No hay un factor único para ese combustible, unidad y año. Revisa el catálogo.')
    return elegibles[0]

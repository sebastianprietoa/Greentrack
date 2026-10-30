"""Preflight, respaldo verificable y migración explícita; DATABASE_URL viene del entorno."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import parse_dsn

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.migracion_integridad import reconciliar_integridad

REQUIRED={
    'usuarios':{'id','empresa','email','password','es_admin','rol','es_demo','anio_default'},
    'registros':{'id','empresa','fecha','fuente','categoria','cantidad','emision','emision_ubicacion'},
    'combustible_movil':{'id','empresa','registro_id','vehiculo_id'},
    'resultados_huella_suministro_agua':{'id','empresa','registro_id','huella_suministro_m3'},
    'agua_sedes':{'id','empresa','activo'},
    'agua_flujos':{'id','empresa','sede_id','periodo','tipo_flujo','volumen_m3','origen_hidrico'},
    'configuracion':{'empresa','medicion_entrada_agua','medicion_salida_agua'},
    'medidas_productivas':{'empresa','anio','unidad','valor'},
    'factores_escasez_agua':{'id','periodo_inicio','periodo_fin','factor_m3eq_m3'},
    'tickets':{'empresa'},'pending_pdf_uploads':{'empresa'},
}


def target_id(dsn):
    fields=parse_dsn(dsn)
    identity='|'.join(fields.get(k,'') for k in ('host','port','dbname','user'))
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def inventory(connection):
    cursor=connection.cursor()
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    result={}
    for (table,) in cursor.fetchall():
        cursor.execute(sql.SQL('SELECT COUNT(*) FROM public.{}').format(sql.Identifier(table)))
        result[table]=cursor.fetchone()[0]
    return result


def preflight(connection):
    cursor=connection.cursor()
    cursor.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public'")
    present={}
    for table,column in cursor.fetchall(): present.setdefault(table,set()).add(column)
    missing={t:sorted(columns-present.get(t,set())) for t,columns in REQUIRED.items() if columns-present.get(t,set())}
    if missing:
        raise RuntimeError('Esquema incompatible: '+json.dumps(missing,ensure_ascii=False))
    cursor.execute('''SELECT COUNT(*),COALESCE(SUM(h.huella_suministro_m3),0)
        FROM resultados_huella_suministro_agua h WHERE NOT EXISTS
        (SELECT 1 FROM registros r WHERE r.id=h.registro_id AND r.empresa=h.empresa)''')
    count,volume=cursor.fetchone()
    cursor.execute("SELECT conname,confupdtype,confdeltype,convalidated FROM pg_constraint WHERE conname IN ('suministro_consumo_empresa_fk','movil_consumo_empresa_fk')")
    constraints=[{'nombre':r[0],'update':r[1],'delete':r[2],'validada':r[3]} for r in cursor.fetchall()]
    return {'huerfanos':count,'volumen_huerfano_m3':str(volume),'claves':constraints}


def tool(name,bin_dir=None):
    executable=Path(bin_dir)/(name+('.exe' if os.name=='nt' else '')) if bin_dir else shutil.which(name)
    if not executable or not Path(executable).is_file(): raise RuntimeError(f'No se encontró {name}; indicar --bin-dir.')
    return str(executable)


def run_tool(command,environment=None):
    result=subprocess.run([str(c) for c in command],env=environment,capture_output=True,text=True,timeout=600)
    if result.returncode: raise RuntimeError(f'{Path(command[0]).name} falló; no se confirmó la operación.')
    return result.stdout


def database_environment(dsn,sslmode):
    mapping={'host':'PGHOST','port':'PGPORT','dbname':'PGDATABASE','user':'PGUSER','password':'PGPASSWORD',
        'sslmode':'PGSSLMODE','options':'PGOPTIONS','sslrootcert':'PGSSLROOTCERT','sslcert':'PGSSLCERT','sslkey':'PGSSLKEY',
        'connect_timeout':'PGCONNECT_TIMEOUT','application_name':'PGAPPNAME'}
    fields=parse_dsn(dsn)
    if set(fields)-set(mapping): raise RuntimeError('La conexión contiene opciones no soportadas por el respaldo.')
    env={k:v for k,v in os.environ.items() if not k.startswith('PG')}
    env.update({mapping[k]:v for k,v in fields.items()})
    env['PGSSLMODE']=sslmode
    env['PGCONNECT_TIMEOUT']='15'
    return env


def backup_database(connection,dsn,folder,bin_dir,sslmode):
    folder=Path(folder).resolve()
    folder.mkdir(parents=True,exist_ok=False)
    dump=folder/'database.dump'
    cursor=connection.cursor()
    cursor.execute('SHOW server_version_num')
    major=int(cursor.fetchone()[0])//10000
    pg_dump=tool('pg_dump',bin_dir)
    version=run_tool([pg_dump,'--version'])
    import re
    if int(re.search(r'(\d+)\.',version)[1])!=major:
        raise RuntimeError('El respaldo requiere herramientas de la misma versión mayor que el servidor.')
    cursor.execute('SELECT pg_export_snapshot()')
    snapshot=cursor.fetchone()[0]
    counts=inventory(connection)
    run_tool([pg_dump,'--format=custom','--no-owner','--no-privileges','--snapshot',snapshot,'--file',dump],database_environment(dsn,sslmode))
    run_tool([tool('pg_restore',bin_dir),'--list',dump])
    manifest={'target_id':target_id(dsn),'created_at':datetime.now(timezone.utc).isoformat(),
        'dump':dump.name,'sha256':hashlib.sha256(dump.read_bytes()).hexdigest(),
        'server_major':major,'list_verified':True,'inventory':counts}
    (folder/'backup.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    return manifest


def verify_backup(path,expected_target):
    path=Path(path).resolve()
    manifest=json.loads(path.read_text(encoding='utf8'))
    if manifest.get('target_id')!=expected_target or not manifest.get('list_verified'):
        raise RuntimeError('El respaldo no corresponde al destino o no está verificado.')
    created=datetime.fromisoformat(manifest['created_at'])
    age=(datetime.now(timezone.utc)-created).total_seconds()
    if age<0 or age>86400: raise RuntimeError('Se requiere un respaldo creado en las últimas 24 horas.')
    dump=(path.parent/manifest['dump']).resolve()
    if dump.parent!=path.parent or hashlib.sha256(dump.read_bytes()).hexdigest()!=manifest['sha256']:
        raise RuntimeError('El archivo de respaldo no coincide con su manifiesto.')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup',metavar='DIRECTORIO_NUEVO')
    parser.add_argument('--bin-dir')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--target-id')
    parser.add_argument('--backup-manifest')
    args=parser.parse_args()
    dsn=os.getenv('DATABASE_URL')
    if not dsn: raise RuntimeError('Falta DATABASE_URL; no se carga .env automáticamente.')
    fields=parse_dsn(dsn)
    local=fields.get('host') in ('127.0.0.1','localhost')
    sslmode=os.getenv('PGSSLMODE','require')
    if not local and sslmode not in ('require','verify-ca','verify-full'):
        raise RuntimeError('Las conexiones remotas requieren SSL.')
    if os.getenv('GREENTRACK_INIT_DB','0')!='0': raise RuntimeError('Desactiva la inicialización automática antes de liberar.')
    identity=target_id(dsn)
    if args.apply:
        if args.backup or args.target_id!=identity or not args.backup_manifest:
            raise RuntimeError('Aplicar exige --target-id correcto y --backup-manifest; el respaldo es un paso separado.')
        verify_backup(args.backup_manifest,identity)
        if not local and os.getenv('GREENTRACK_ALLOW_RELEASE_MIGRATION')!='1':
            raise RuntimeError('La migración remota requiere habilitación explícita del operador.')
    connection=psycopg2.connect(dsn,sslmode=sslmode,connect_timeout=15)
    try:
        connection.set_session(readonly=not args.apply,isolation_level='READ COMMITTED' if args.apply else 'REPEATABLE READ')
        cursor=connection.cursor()
        cursor.execute("SET LOCAL lock_timeout='10s'")
        cursor.execute("SET LOCAL statement_timeout='120s'")
        plan=preflight(connection)
        result={'target_id':identity,'remoto':not local,'apply':args.apply,'antes':plan}
        if args.backup:
            result['backup']=backup_database(connection,dsn,args.backup,args.bin_dir,sslmode)
        if args.apply:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('greentrack_release_integridad_v1'))")
            result['migracion']=reconciliar_integridad(connection)
            result['despues']=preflight(connection)
            if result['despues']['huerfanos'] or len(result['despues']['claves'])!=2 or any(
                r['update']!='c' or r['delete']!='c' or not r['validada'] for r in result['despues']['claves']):
                raise RuntimeError('No se cumplieron las condiciones posteriores de integridad.')
            connection.commit()
        else:
            connection.rollback()
        print(json.dumps(result,ensure_ascii=False))
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(str(exc) if isinstance(exc,RuntimeError) else 'No se completó la operación; revisar conexión, archivos y herramientas.',file=sys.stderr)
        sys.exit(1)

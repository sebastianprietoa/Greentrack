"""Ensaya respaldo, migración y recuperación en DOS bases locales nuevas."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from dotenv import dotenv_values
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn, parse_dsn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.release_database import database_environment, inventory, run_tool, tool, target_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_dump', type=Path)
    parser.add_argument('--bin-dir', required=True)
    args = parser.parse_args()
    dsn = dotenv_values(ROOT / '.env.staging').get('TEST_DATABASE_URL')
    fields = parse_dsn(dsn or '')
    if fields.get('host') != '127.0.0.1' or fields.get('port') != '55432' or fields.get('dbname') != 'greentrack_staging':
        raise RuntimeError('El ensayo solo permite el clúster local de staging en 55432.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    folder = ROOT / 'tmp' / ('release_rehearsal_' + stamp)
    folder.mkdir(parents=True, exist_ok=False)
    lab = 'greentrack_release_' + stamp
    recovery = lab + '_recovery'
    admin = psycopg2.connect(dsn, sslmode='disable', connect_timeout=5)
    admin.autocommit = True
    try:
        for name in (lab, recovery):
            # CREATE rechaza nombres existentes; nunca se elimina/sobrescribe una base.
            with admin.cursor() as cursor:
                cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    finally:
        admin.close()
    lab_dsn = make_dsn(dsn, dbname=lab)
    recovery_dsn = make_dsn(dsn, dbname=recovery)
    restore = tool('pg_restore', args.bin_dir)
    run_tool([restore, '--exit-on-error', '--no-owner', '--no-privileges', '--dbname', lab,
              args.source_dump.resolve()], database_environment(lab_dsn, 'disable'))
    env = dict(os.environ, DATABASE_URL=lab_dsn, PGSSLMODE='disable', GREENTRACK_INIT_DB='0')

    def release(*arguments):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/release_database.py'), *arguments],
                                env=env, capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise RuntimeError('Falló el ensayo de release: ' + result.stderr.strip())
        return json.loads(result.stdout)

    before = release()
    backup = release('--backup', str(folder / 'backup'), '--bin-dir', args.bin_dir)
    manifest = folder / 'backup' / 'backup.json'
    applied = release('--apply', '--target-id', target_id(lab_dsn), '--backup-manifest', str(manifest))
    repeated = release('--apply', '--target-id', target_id(lab_dsn), '--backup-manifest', str(manifest))
    run_tool([restore, '--exit-on-error', '--no-owner', '--no-privileges', '--dbname', recovery,
              folder / 'backup' / 'database.dump'], database_environment(recovery_dsn, 'disable'))
    connection = psycopg2.connect(recovery_dsn, sslmode='disable', connect_timeout=5)
    try:
        restored = inventory(connection)
    finally:
        connection.close()
    if restored != backup['backup']['inventory']:
        raise RuntimeError('La restauración no conserva el inventario completo.')
    connection = psycopg2.connect(lab_dsn, sslmode='disable', connect_timeout=5)
    try:
        after_inventory = inventory(connection)
    finally:
        connection.close()
    if after_inventory['registros'] != restored['registros']:
        raise RuntimeError('La migración alteró el número de registros maestros.')
    result = {'before': before, 'applied': applied, 'repeated': repeated,
              'restored_inventory_matches': True, 'master_records_unchanged': True,
              'tables_restored': len(restored), 'lab_database': lab,
              'recovery_database': recovery, 'backup_manifest': str(manifest),
              'production_modified': False}
    report = ROOT / 'outputs/estabilizacion/ensayo_liberacion.json'
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()

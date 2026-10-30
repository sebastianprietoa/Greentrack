"""Guardas del respaldo y selección del candidato sin conexiones externas."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from scripts.release_database import database_environment, target_id, verify_backup
from scripts.build_release_candidate import selected_files, ROOT_FILES, EXTRA_FILES, DIRECTORIES
from tests.test_integridad_postgres import pg


def test_admin_initialization_requires_explicit_password_and_preserves_existing(monkeypatch, pg):
    from app import _seed_admin_account
    from auth_utils import verify_password
    cursor = pg.cursor()
    cursor.execute('''CREATE TEMP TABLE usuarios (empresa TEXT, email TEXT UNIQUE,
        password TEXT, contacto TEXT, es_admin INTEGER, rol TEXT, es_demo BOOLEAN, fecha_registro TEXT)''')
    monkeypatch.delenv('ADMIN_INITIAL_PASSWORD', raising=False)
    _seed_admin_account(cursor)
    cursor.execute('SELECT COUNT(*) FROM usuarios')
    assert cursor.fetchone()[0] == 0
    monkeypatch.setenv('ADMIN_INITIAL_PASSWORD', 'synthetic-initial-password')
    _seed_admin_account(cursor)
    cursor.execute('SELECT password,rol,es_admin FROM usuarios')
    original, role, is_admin = cursor.fetchone()
    assert role == 'admin' and is_admin == 1
    assert verify_password('synthetic-initial-password', original)
    monkeypatch.setenv('ADMIN_INITIAL_PASSWORD', 'different-initial-password')
    _seed_admin_account(cursor)
    cursor.execute('SELECT password FROM usuarios')
    assert cursor.fetchall() == [(original,)]


def test_target_identity_ignores_password():
    assert target_id('host=localhost port=55432 dbname=test user=operator password=one') == target_id(
        'host=localhost port=55432 dbname=test user=operator password=two')
    assert target_id('host=localhost dbname=a') != target_id('host=localhost dbname=b')


def test_dump_credentials_only_in_environment(monkeypatch):
    monkeypatch.setenv('PGSERVICE', 'unexpected')
    env = database_environment('host=localhost dbname=test user=operator password=example', 'disable')
    assert env['PGPASSWORD'] == 'example'
    assert env['PGSSLMODE'] == 'disable'
    assert 'PGSERVICE' not in env


@pytest.mark.parametrize('failure', ['target', 'hash', 'old', 'escape', 'unverified', None])
def test_backup_guards(tmp_path, failure):
    content = b'synthetic backup'
    (tmp_path / 'database.dump').write_bytes(content)
    manifest = {'target_id': 'expected', 'list_verified': True, 'dump': 'database.dump',
                'sha256': hashlib.sha256(content).hexdigest(),
                'created_at': datetime.now(timezone.utc).isoformat()}
    if failure == 'target': manifest['target_id'] = 'other'
    if failure == 'hash': manifest['sha256'] = 'invalid'
    if failure == 'old': manifest['created_at'] = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    if failure == 'escape': manifest['dump'] = '../database.dump'
    if failure == 'unverified': manifest['list_verified'] = False
    path = tmp_path / 'backup.json'
    path.write_text(json.dumps(manifest), encoding='utf8')
    if failure:
        with pytest.raises(RuntimeError): verify_backup(path, 'expected')
    else:
        assert verify_backup(path, 'expected') == manifest


def test_candidate_excludes_private_and_work_files(tmp_path):
    for name in ROOT_FILES + EXTRA_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('example', encoding='utf8')
    for name in DIRECTORIES:
        (tmp_path / name).mkdir(exist_ok=True)
    for name in ('.env', '.env.staging', 'database.dump', 'private.pdf'):
        (tmp_path / name).write_text('excluded', encoding='utf8')
    names = {p.relative_to(tmp_path).as_posix() for p in selected_files(tmp_path)}
    assert not names.intersection({'.env', '.env.staging', 'database.dump', 'private.pdf'})
    assert 'data/factores_energia_chile_codex_v1.json' in names
    assert 'assets/sinader_codigos_group21.xlsx' in names

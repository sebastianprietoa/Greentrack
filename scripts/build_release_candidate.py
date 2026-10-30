"""Empaqueta fuentes explícitas; excluye secretos, respaldos y archivos de trabajo."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ('app.py', 'auth_utils.py', 'db.py', 'config.py', 'extractor_sidrep.py',
              'extractor_sinader.py', 'requirements.txt', 'requirements-dev.txt',
              'Procfile', '.env.example')
DIRECTORIES = ('routes', 'services', 'utils', 'templates', 'static', 'tests')
EXTRA_FILES = ('data/factores_energia_chile_codex_v1.json',
               'assets/sinader_codigos_group21.xlsx', 'scripts/release_database.py',
               'scripts/build_release_candidate.py', 'scripts/rehearsal_release_staging.py',
               '.github/workflows/ci.yml', 'docs/liberacion_estabilizacion.md')


def selected_files(root):
    paths = [root / name for name in ROOT_FILES + EXTRA_FILES]
    for directory in DIRECTORIES:
        paths.extend(p for p in (root / directory).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo'))
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f'Archivo requerido ausente o enlace: {path.relative_to(root)}')
        if path.name.startswith('.env') and path.name != '.env.example':
            raise RuntimeError('El candidato contiene una configuración privada.')
    return sorted(set(paths))


def build(root, destination):
    files = selected_files(root)
    for path in files:
        if path.suffix in ('.xlsx', '.woff', '.woff2', '.ttf', '.png', '.jpg', '.jpeg', '.ico', '.pdf'):
            continue
        content = path.read_bytes().decode('utf8', errors='replace')
        if path.relative_to(root).as_posix() == '.github/workflows/ci.yml':
            content = content.replace('postgresql://' + 'postgres:$DB_PASSWORD@127.0.0.1:55432/greentrack_staging', '')
        if path.name != '.env.example' and re.search(
                r'postgres(?:ql)?://[^\s:/]+:[^\s@]+@|ghp_[A-Za-z0-9]{20,}|sk-proj-[A-Za-z0-9_-]{20,}', content):
            raise RuntimeError(f'Posible credencial: revisar {path.relative_to(root)}')
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(),
                'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
                'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=root, text=True).strip(),
                'working_tree_candidate': True, 'files': {}}
    archive = destination / 'greentrack-rc.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for path in files:
            relative = path.relative_to(root).as_posix()
            content = path.read_bytes()
            manifest['files'][relative] = hashlib.sha256(content).hexdigest()
            output.writestr(relative, content)
    manifest['archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    diff = subprocess.check_output(['git', 'diff', '--ignore-space-at-eol', '--',
                                    *ROOT_FILES, *DIRECTORIES, 'data', 'assets', 'scripts', 'docs'], cwd=root)
    (destination / 'review-tracked.diff').write_bytes(diff)
    return {'archive': str(archive), 'sha256': manifest['archive_sha256'], 'files': len(files)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='Directorio nuevo; nunca se sobrescribe')
    args = parser.parse_args()
    print(json.dumps(build(ROOT, args.destination.resolve())))

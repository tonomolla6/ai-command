"""Fetch stable GitHub release assets, verify SHA-256, then activate locally."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import urllib.error
import urllib.request

from .core import ManagerError, read_json
from .distribution import installation, install, rollback, version_tuple

REPO = 'tonomolla6/ai-command'
API = 'https://api.github.com/repos/' + REPO
MAX_ARCHIVE = 32 * 1024 * 1024


def download(url, limit, timeout=30, accept=None):
    request = urllib.request.Request(url, headers={'User-Agent': 'ai-command-updater',
        'Accept': accept or ('application/vnd.github+json' if url.startswith(API) else 'application/octet-stream')})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(limit + 1)
    except (urllib.error.URLError, TimeoutError):
        raise ManagerError('No se pudo descargar la release de GitHub; instalación actual conservada') from None
    if len(data) > limit:
        raise ManagerError('La descarga excede el tamaño permitido')
    return data


def release_info(tag=None, timeout=30):
    if tag:
        version_tuple(tag)
    data = json.loads(download(API + ('/releases/tags/' + tag if tag else '/releases/latest'), 1024 * 1024, timeout=timeout))
    version_tuple(data['tag_name'])
    if data.get('draft') or data.get('prerelease'):
        raise ManagerError('Sólo se admiten releases estables publicadas')
    return data


def extract_verified(blob, expected, dest):
    if len(blob) > MAX_ARCHIVE or hashlib.sha256(blob).hexdigest() != expected:
        raise ManagerError('SHA-256 incorrecto; instalación actual conservada')
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as tar:
        members = tar.getmembers()
        names = set()
        if len(members) > 3000 or sum(m.size for m in members) > 64 * 1024 * 1024:
            raise ManagerError('Paquete excesivo')
        for m in members:
            p = Path(m.name)
            if p.is_absolute() or '..' in p.parts or m.name in names or not (m.isfile() or m.isdir()):
                raise ManagerError('Contenido inseguro en el paquete; no se instala')
            names.add(m.name)
        for m in members:
            target = dest / m.name
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, target.open('xb') as out:
                    out.write(src.read())


def update(args):
    base = installation()
    current = read_json(base / 'install.json')
    if not base.stat().st_uid == __import__('os').getuid():
        raise ManagerError('Instalación global: ejecuta sudo ai update; los datos de usuario no se trasladan')
    if args.rollback:
        result = rollback(base)
        print('Restaurada ' + result['version'] + '. Cuentas e historiales conservados.')
        return 0
    if args.list:
        for path in sorted((base / 'releases').iterdir()):
            if not path.name.startswith('.'):
                print(('✓ ' if path.name == current['active'] else '  ') + path.name)
        return 0
    info = release_info(args.to)
    tag = info['tag_name']
    print('AI UPDATE · ' + current['version'] + ' → ' + tag)
    if version_tuple(tag) < version_tuple(current['version']):
        raise ManagerError('Para volver a una versión instalada usa ai update --rollback')
    if current['version'] == tag:
        print('Ya tienes la última versión estable.')
        return 0
    if args.check:
        print('Disponible. Ejecuta ai update para instalarla.')
        return 0
    name = 'ai-command-' + tag + '.tar.gz'
    assets = {a['name']: a for a in info.get('assets', [])}
    if name not in assets or 'SHA256SUMS' not in assets:
        raise ManagerError('Release incompleta: faltan paquete/checksums; no se modifica la instalación')
    prefix = 'https://github.com/' + REPO + '/releases/download/' + tag + '/'
    for key in (name, 'SHA256SUMS'):
        if assets[key].get('browser_download_url') != prefix + key:
            raise ManagerError('Origen del paquete inesperado')
    sums = download(prefix + 'SHA256SUMS', 16384).decode().splitlines()
    matches = [line.split()[0] for line in sums if len(line.split()) == 2 and line.split()[1].lstrip('*') == name]
    if len(matches) != 1 or not __import__('re').fullmatch('[0-9a-f]{64}', matches[0]):
        raise ManagerError('Checksum de release no válido')
    expected = matches[0]
    digest = assets[name].get('digest')
    if digest and digest != 'sha256:' + expected:
        raise ManagerError('GitHub y SHA256SUMS no coinciden')
    blob = download(prefix + name, MAX_ARCHIVE)
    with tempfile.TemporaryDirectory(prefix='ai-command-update-') as temporary:
        source = Path(temporary)
        extract_verified(blob, expected, source)
        if 'v' + (source / 'VERSION').read_text().strip() != tag:
            raise ManagerError('Versión del paquete diferente al tag')
        # Execute the installer shipped by the NEW release, so future installer
        # and migration changes are not limited by the old implementation.
        import subprocess, sys
        command = [sys.executable, str(source / 'install.py'), '--prefix', current['prefix']]
        if not current.get('auto', True):
            command.append('--no-auto')
        subprocess.run(command, check=True, timeout=120)
    print('Actualizado a ' + tag + '. Reversión: ai update --rollback')
    return 0

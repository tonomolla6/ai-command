"""Transactional installation of immutable, architecture-independent releases."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .core import Manager, ManagerError, atomic_write, now, read_json
from .migrations import CURRENT, plan

TAG = re.compile(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z')
FILES = ('ai_manager', 'integrations', 'skills')
TEXT_FILES = ('README.md', 'LICENSE', 'CHANGELOG.md', 'VERSION')


def version_tuple(tag):
    match = TAG.fullmatch(tag)
    if not match:
        raise ManagerError('Versión no válida; usa vMAJOR.MINOR.PATCH')
    return tuple(map(int, match.groups()))


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def payload_files(source):
    source = Path(source)
    paths = [source / name for name in TEXT_FILES if (source / name).is_file()]
    for name in FILES:
        paths.extend(p for p in (source / name).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix != '.pyc')
    if any(p.is_symlink() for p in paths):
        raise ManagerError('El paquete contiene enlaces no permitidos')
    return sorted(paths)


def installation(root=None):
    root = Path(root or Path(__file__).resolve().parent.parent)
    if (root / '.release.json').is_file() and root.parent.name == 'releases':
        return root.parent.parent
    raise ManagerError('Instalación de desarrollo: ejecuta install.sh antes de usar ai update')


@contextlib.contextmanager
def install_lock(base):
    base.mkdir(parents=True, exist_ok=True)
    fd = os.open(base / '.install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ManagerError('Hay otra instalación/actualización en curso') from None
        yield
    finally:
        os.close(fd)


def point_current(base, release):
    temporary = base / ('.current-' + str(os.getpid()))
    try:
        temporary.symlink_to(release.relative_to(base))
        os.replace(temporary, base / 'current')
    finally:
        temporary.unlink(missing_ok=True)


def own_launcher(path):
    try:
        data = path.read_text()[:4096]
        return ('# ai-command managed launcher v1' in data or
                '# ai-manager provider entrypoint v1' in data or
                'from ai_manager.cli import main' in data or
                'from ai_manager.entrypoints import provider_main' in data or
                bool(re.fullmatch(r'#!/bin/sh\nexec [^\n]+/ai (?:resume )?(?:codex|claude) [0-9]+ "\$@"\n', data)))
    except (OSError, UnicodeError):
        return False


def launcher_contents(prefix, accounts, auto=True):
    base = prefix / 'lib/ai-command'
    bin_dir = prefix / 'bin'
    common = '#!' + sys.executable + '\n# ai-command managed launcher v1\nimport sys\nsys.path.insert(0, ' + repr(str(base / 'current')) + ')\n'
    launchers = {'ai': common + 'from ai_manager.cli import main\nraise SystemExit(main())\n'}
    for provider in ('codex', 'claude') if auto else ():
        launchers[provider] = common + '# ai-manager provider entrypoint v1\nfrom ai_manager.entrypoints import provider_main\nraise SystemExit(provider_main(' + repr(provider) + '))\n'
    import shlex
    for a in accounts:
        if a['provider'] not in ('codex', 'claude') or not str(a['account']).isdigit():
            raise ManagerError('Registro de cuentas no válido')
        provider, number = a['provider'], str(a['account'])
        for name in (('x' if provider == 'codex' else 'c') + number, provider + number):
            launchers[name] = '#!/bin/sh\nexec ' + shlex.quote(str(bin_dir / 'ai')) + ' ' + provider + ' ' + number + ' "$@"\n'
            launchers[name + 'r'] = '#!/bin/sh\nexec ' + shlex.quote(str(bin_dir / 'ai')) + ' resume ' + provider + ' ' + number + ' "$@"\n'
    return launchers


def install(source, prefix, auto=True):
    source, prefix = Path(source).resolve(), Path(prefix).expanduser().absolute()
    if sys.version_info < (3, 11):
        raise ManagerError('Se requiere Python 3.11 o posterior')
    tag = 'v' + (source / 'VERSION').read_text().strip()
    version_tuple(tag)
    base, bin_dir = prefix / 'lib/ai-command', prefix / 'bin'
    if base.is_symlink() or bin_dir.is_symlink():
        raise ManagerError('El prefijo de instalación debe usar directorios reales')
    manager = Manager()
    if manager.config:
        plan(manager.config)  # Refuse future schemas before touching installation.
    paths = payload_files(source)
    hashes = {str(p.relative_to(source)): file_hash(p) for p in paths}
    if not {'ai_manager/cli.py', 'ai_manager/distribution.py', 'VERSION'} <= hashes.keys():
        raise ManagerError('Paquete incompleto')
    package_id = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    release_name = tag + '-' + package_id[:12]
    launchers = launcher_contents(prefix, manager.accounts(include_inactive=True), auto)
    import shlex
    for name in ('terminales','terminales-native','terminal-estado'):
        if not (source/'integrations/vscode/bin'/name).exists():continue
        target=prefix/'bin'/name
        # Preserve pre-existing third-party/local terminal helpers.
        if target.exists() and not own_launcher(target):continue
        interpreter='/bin/bash' if name=='terminales' else sys.executable
        launchers[name]='#!/bin/sh\n# ai-command managed launcher v1\nexec '+shlex.quote(interpreter)+' '+shlex.quote(str(base/'current/integrations/vscode/bin'/name))+' "$@"\n'
    bin_dir.mkdir(parents=True, exist_ok=True)
    originals = {}
    backups = {}
    for name, content in launchers.items():
        target = bin_dir / name
        if target.exists() or target.is_symlink():
            if own_launcher(target):
                backups[name] = target.read_bytes()
            elif name in ('codex', 'claude'):
                # Capture a native binary/symlink before replacing only its PATH
                # entry. The original stays accessible under native/ below.
                originals[name] = target.resolve()
                if str(originals[name]).startswith(str(base)):
                    raise ManagerError('Original nativo ambiguo: ' + str(target))
            else:
                raise ManagerError('Comando ajeno conservado: ' + str(target))
    # Existing native binaries must stay at their original pathname: wrappers go
    # in a different prefix, unless a symlink can point to its retained target.
    for name, original in originals.items():
        if not (bin_dir / name).is_symlink():
            raise ManagerError('Binario nativo en ' + str(bin_dir / name) + '; usa otro --prefix o --no-auto')
    with install_lock(base):
        releases = base / 'releases'
        releases.mkdir(exist_ok=True)
        release = releases / release_name
        old_meta = read_json(base / 'install.json')
        native = base / 'native'
        native.mkdir(exist_ok=True)
        # Reuse originals retained by older installations, without copying auth.
        from .providers import executable
        for provider in ('codex', 'claude', 'agy', 'opencode'):
            p = native / provider
            if not p.exists():
                try:
                    original = originals.get(provider) or Path(executable(provider)).resolve()
                    if original != bin_dir / provider:
                        p.symlink_to(original)
                except ManagerError:
                    pass
        if not release.exists():
            stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=releases))
            try:
                for path in paths:
                    dest = stage / path.relative_to(source)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, dest)
                    dest.chmod(0o755 if path.parent.name == 'bin' else 0o644)
                (stage / 'native').symlink_to(native)
                atomic_write(stage / '.release.json', json.dumps({'version': tag, 'id': package_id,
                    'config_schema_max': CURRENT, 'files': hashes}, indent=2), 0o644)
                for directory in [stage, *[p for p in stage.rglob('*') if p.is_dir() and not p.is_symlink()]]:
                    directory.chmod(0o755)
                subprocess.run([sys.executable, '-c',
                    'import sys;sys.path.insert(0,sys.argv[1]);from ai_manager.cli import parser;parser()' , str(stage)],
                    check=True, capture_output=True, timeout=20)
                os.rename(stage, release)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        validate_release(release)
        backup = base / 'backups' / dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        if backups or old_meta:
            backup.mkdir(parents=True, mode=0o700)
            for name, data in backups.items():
                (backup / name).write_bytes(data)
            if old_meta:
                (backup / 'install.json').write_text(json.dumps(old_meta, indent=2))
        old_current = (base / 'current').resolve() if (base / 'current').is_symlink() else None
        if (base / 'current').exists() and not (base / 'current').is_symlink():
            raise ManagerError('current no es un enlace administrado; no se modifica')
        try:
            if old_current is None:point_current(base, release)
            # Stable launchers are written before the switch. Existing managed
            # launchers still resolve old current until the final atomic rename.
            for name, content in launchers.items():
                target = bin_dir / name
                if target.is_symlink():
                    target.unlink()  # Native target itself remains untouched.
                atomic_write(target, content, 0o755)
            point_current(base, release)
            metadata = {'app': 'ai-command', 'version': tag, 'active': release_name,
                        'previous': old_meta.get('active') if old_meta.get('active') != release_name else old_meta.get('previous'),
                        'prefix': str(prefix), 'python': sys.executable, 'auto': auto, 'updated_at': now()}
            atomic_write(base / 'install.json', json.dumps(metadata, indent=2) + '\n', 0o644)
        except BaseException:
            if old_current:
                point_current(base, old_current)
            for name, data in backups.items():
                atomic_write(bin_dir / name, data.decode(), 0o755)
            for name, original in originals.items():
                (bin_dir / name).unlink(missing_ok=True)
                (bin_dir / name).symlink_to(original)
            raise
    return metadata


def validate_release(release):
    release = Path(release)
    meta = read_json(release / '.release.json')
    if not meta.get('files'):
        raise ManagerError('Metadatos de versión ausentes')
    for name, expected in meta['files'].items():
        path = release / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or file_hash(path) != expected:
            raise ManagerError('La instalación fue modificada: ' + name)
    return meta


def rollback(base):
    base = Path(base)
    with install_lock(base):
        meta = read_json(base / 'install.json')
        previous = meta.get('previous')
        if not previous or Path(previous).name != previous:
            raise ManagerError('No hay una versión anterior instalada')
        release = base / 'releases' / previous
        target = validate_release(release)
        config = Manager().config
        if config.get('manager_schema', 0) > target.get('config_schema_max', 0):
            raise ManagerError('La versión anterior no admite esta configuración; se conserva la actual')
        old = base / 'releases' / meta['active']
        point_current(base, release)
        meta.update(previous=meta['active'], active=previous, version=target['version'], updated_at=now())
        try:atomic_write(base / 'install.json', json.dumps(meta, indent=2) + '\n', 0o644)
        except BaseException:
            point_current(base, old)
            raise
        return meta

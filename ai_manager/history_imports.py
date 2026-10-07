"""Private transcript snapshots; restore verbatim on demand, never convert a DB."""
from __future__ import annotations

import gzip
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .core import ManagerError, private_dir, read_json

UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$', re.I)
RESERVE = 15 * 1024**3


def imported_sessions(manager, provider):
    rows = []
    for name in manager.config.get('history_imports', []):
        manifest = Path(name)
        if manifest.is_symlink() or not manifest.is_file():
            continue
        if manifest.stat().st_uid != os.getuid() or manifest.stat().st_size > 16 * 1024**2:
            raise ManagerError('Índice de importación no privado o demasiado grande')
        data = read_json(manifest)
        if data.get('schema') != 1:
            continue
        for row in data.get('sessions', []):
            if (row.get('provider') != provider or not row.get('selectable',True) or
                    not UUID.fullmatch(str(row.get('id', '')))):
                continue
            archive = Path(row.get('archive', ''))
            if archive.is_absolute() or '..' in archive.parts or not archive.parts:
                raise ManagerError('Ruta de snapshot fuera de su importación')
            archive = manifest.parent / archive
            try:
                archive.resolve().relative_to(manifest.parent.resolve())
            except ValueError:
                raise ManagerError('Snapshot enlazado fuera de su importación') from None
            if not archive.is_file() or archive.is_symlink():
                continue
            rows.append({**row, 'path': str(archive), 'snapshot_manifest': str(manifest),
                         'origin_account': str(row.get('account', 'importada'))})
    return rows


def mapped_directory(manager, cwd):
    """Explicit host path mappings, without changing the native transcript."""
    for old, new in sorted(manager.config.get('cwd_aliases', {}).items(),
                           key=lambda pair: len(pair[0]), reverse=True):
        try:
            relative = Path(cwd).relative_to(old)
        except ValueError:
            continue
        return str(Path(new) / relative)
    return cwd


def restored_path(manager, account, session):
    if account['provider'] == 'codex':
        # An independent native index avoids selecting an older rollout with the
        # same ID in the destination's existing database. Codex builds it itself.
        relative=Path(session.get('relative_path',''))
        if (relative.is_absolute() or '..' in relative.parts or not relative.parts or
                not relative.name.startswith('rollout-') or not relative.name.endswith(session['id']+'.jsonl')):
            raise ManagerError('Ruta nativa Codex no válida en la importación')
        return Path(account['home']) / 'sessions' / relative
    return manager.state / 'restored' / 'claude' / (session['id'] + '.jsonl')


def restore_snapshot(manager, account, session, dry_run=False):
    target = restored_path(manager, account, session)
    if dry_run:
        return target
    with manager.lock('restore-' + account['provider'] + '-' + session['id'], timeout=15):
        if target.is_symlink():
            raise ManagerError('El destino de restauración es un enlace; no se sobrescribe')
        if target.exists():
            # A resumed transcript has new native events. Never overwrite them
            # with the original imported snapshot on a later launch.
            with target.open() as stream:
                first = json.loads(stream.readline(1024 * 1024))
            ident = first.get('payload', {}).get('id') if account['provider'] == 'codex' else first.get('sessionId')
            if account['provider'] == 'codex' and ident != session['id']:
                raise ManagerError('El transcript restaurado pertenece a otra sesión')
            return target
        size = session.get('bytes')
        digest = session.get('sha256', '')
        if not isinstance(size, int) or size <= 0 or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ManagerError('Snapshot sin tamaño o checksum verificable')
        native=session.get('compression')=='none'
        base=None
        if session.get('compression')=='append-gzip':
            relative=Path(session.get('base_archive',''))
            if relative.is_absolute() or '..' in relative.parts or not relative.parts:
                raise ManagerError('Base de snapshot fuera de la importación')
            base=Path(session['snapshot_manifest']).parent/relative
            if base.is_symlink() or not base.is_file() or base.stat().st_size!=session.get('base_bytes'):
                raise ManagerError('Base de snapshot ausente o modificada')
            try:base.resolve().relative_to(Path(session['snapshot_manifest']).parent.resolve())
            except ValueError:raise ManagerError('Base de snapshot fuera de la importación') from None
        needed=1024*1024 if native else size-(base.stat().st_size if base else 0)
        if shutil.disk_usage(manager.home).free - needed < RESERVE:
            raise ManagerError('Restaurar esta conversación dejaría menos de 15 GiB libres; '
                               'el snapshot se conserva. Usa otra sesión o un handoff.')
        # Check each parent before creating it; never traverse a profile symlink.
        parent = manager.home
        try:
            parts = target.parent.relative_to(parent).parts
        except ValueError:
            raise ManagerError('Restauración fuera del HOME del usuario') from None
        for part in parts:
            parent = private_dir(parent / part)
        fd, tmp = tempfile.mkstemp(prefix='.restore-', dir=target.parent)
        try:
            count = 0
            check = hashlib.sha256()
            opener=open if native else gzip.open
            with os.fdopen(fd, 'wb') as out, opener(session['path'], 'rb') as source:
                cloned=False
                if base:
                    with base.open('rb') as original:
                        try:fcntl.ioctl(out.fileno(),0x40049409,original.fileno())
                        except OSError:
                            if shutil.disk_usage(manager.home).free-size<RESERVE:
                                raise ManagerError('No hay espacio para reconstruir sin reflink') from None
                            shutil.copyfileobj(original,out)
                        original.seek(0)
                        prefix=hashlib.sha256()
                        while chunk:=original.read(1024*1024):
                            count+=len(chunk);check.update(chunk);prefix.update(chunk)
                        if prefix.hexdigest()!=session.get('base_sha256'):
                            raise ManagerError('Checksum de la base de transcript inválido')
                        out.seek(0,os.SEEK_END)
                if native:
                    try:
                        fcntl.ioctl(out.fileno(),0x40049409,source.fileno()) # Linux FICLONE: private copy-on-write snapshot.
                        cloned=True
                    except OSError:
                        if shutil.disk_usage(manager.home).free-size<RESERVE:
                            raise ManagerError('No hay espacio para una copia nativa sin reflink') from None
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    if count > size:
                        raise ManagerError('Snapshot excede su tamaño declarado')
                    check.update(chunk)
                    if not cloned:out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if count != size or check.hexdigest() != digest:
                raise ManagerError('Checksum de transcript inválido; no se ha restaurado')
            os.replace(tmp, target)
            os.utime(target, (session['updated'], session['updated']))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return target


def imported_resume(manager, account, session, dry_run=False):
    if account['provider'] == 'claude':
        path = restore_snapshot(manager, account, session, dry_run)
        return ['--resume', str(path)]
    native_state = manager.state / 'restored' / 'codex-index'
    # Once Codex has indexed the restored transcript, all accounts reuse that
    # native path, including its new events. Never restore a stale second copy.
    from .sessions import codex_sessions
    indexed=next((r for r in codex_sessions(native_state) if r['id']==session['id']),None)
    if indexed is None:
        restore_snapshot(manager, account, session, dry_run)
    if not dry_run:
        private_dir(native_state.parent)
        private_dir(native_state)
    return ['-c', 'sqlite_home=' + json.dumps(str(native_state)),
            'resume', session['id'], '-C', str(Path.cwd())]

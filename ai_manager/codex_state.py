"""Read-only routing of a native Codex thread and its existing goal store."""
from __future__ import annotations

import contextlib
from pathlib import Path
import sqlite3

from .core import ManagerError


def index_thread(directory, ident):
    """Read only identity/path/recency, never a goal objective or transcript body."""
    directory = Path(directory).absolute()
    for db in sorted(directory.glob('state_*.sqlite'), reverse=True):
        if db.is_symlink():
            raise ManagerError('Índice Codex enlazado: no se cambia el estado del goal')
        try:
            with contextlib.closing(sqlite3.connect(db.as_uri() + '?mode=ro',
                                                   uri=True, timeout=2)) as conn:
                columns = {row[1] for row in conn.execute('pragma table_info(threads)')}
                if not {'id', 'rollout_path', 'updated_at'} <= columns:
                    continue
                row = conn.execute('select rollout_path,updated_at from threads where id=?',
                                   (ident,)).fetchone()
                if row and row[0] and Path(row[0]).is_file():
                    return {'path': row[0], 'updated': float(row[1] or 0),
                            'sqlite_home': str(directory)}
                return None
        except (sqlite3.Error, OSError, ValueError):
            raise ManagerError('No se pudo leer el índice Codex; se conserva el goal') from None
    return None


def has_goal(directory, ident):
    for db in sorted(Path(directory).absolute().glob('goals_*.sqlite'), reverse=True):
        if db.is_symlink():
            raise ManagerError('Base de goals enlazada: no se cambia de índice')
        try:
            with contextlib.closing(sqlite3.connect(db.as_uri() + '?mode=ro',
                                                   uri=True, timeout=2)) as conn:
                return conn.execute('select 1 from thread_goals where thread_id=?',
                                    (ident,)).fetchone() is not None
        except (sqlite3.Error, OSError):
            raise ManagerError('No se pudo comprobar el goal Codex; no se cambia de índice') from None
    return False


def goal_index(manager, account, ident, preferred, path=None, snapshot_updated=None):
    """Keep the store holding the goal, only when the native history is the same.

    sqlite_home also controls goals_*.sqlite. Imported rollouts can already
    be indexed in the common store: redirecting them to the restoration store
    would hide their goal. Do not copy/merge native databases or recreate goals,
    which would discard their original budget, counters and lifecycle state.
    """
    preferred = Path(preferred).absolute()
    directories = {preferred, Path(account.get('shared_sqlite_home') or account['home']).absolute(),
                   (manager.state / 'restored/codex-index').absolute()}
    holders = [directory for directory in sorted(directories) if has_goal(directory, ident)]
    if len(holders) > 1:
        raise ManagerError('El goal de este hilo existe en varios índices Codex. '
                           'No se mezclan objetivos, presupuestos ni historiales; '
                           'conserva la sesión abierta y revisa los índices antes de reanudar.')
    if not holders or holders[0] == preferred:
        return preferred
    holder = holders[0]
    thread = index_thread(holder, ident)
    if thread is None:
        raise ManagerError('El goal existe en otro índice sin una conversación accesible; '
                           'no se crea un objetivo nuevo ni se pierde el anterior.')
    if path is not None:
        try:
            identical = Path(thread['path']).samefile(path)
        except OSError:
            identical = False
        if not identical:
            raise ManagerError('El goal y la conversación restaurada tienen historiales distintos. '
                               'No se cambia de índice ni se sobrescribe el objetivo.')
    elif snapshot_updated is None or thread['updated'] < snapshot_updated:
        raise ManagerError('El snapshot es posterior al historial que contiene el goal. '
                           'No se sustituye la conversación ni se reinicia el objetivo.')
    return holder

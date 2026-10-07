"""Versioned, per-user migrations of manager metadata, never provider files."""
from __future__ import annotations

import copy

from .core import ManagerError, backup_files, now, read_json, write_json

CURRENT = 1


def plan(config):
    if config.get('schema') != 1:
        raise ManagerError('Formato de registro no soportado; no se modifica')
    old = config.get('manager_schema', 0)
    if type(old) is not int or old < 0 or old > CURRENT:
        raise ManagerError('Configuración de una versión más reciente; actualiza el gestor')
    result = copy.deepcopy(config)
    steps = []
    if old < 1:
        # Preserve the behaviour of the pre-release manager on existing servers.
        # New installations explicitly default to normal Claude permissions.
        result.setdefault('claude_skip_permissions', True)
        result['manager_schema'] = 1
        steps.append('001: registrar política existente de permisos de Claude')
    return result, steps


def migrate(manager, dry_run=False):
    if not manager.config_path.exists():
        return []
    with manager.lock('registry', timeout=15):
        config = read_json(manager.config_path)
        result, steps = plan(config)
        if steps and not dry_run:
            backup = backup_files(manager.home, [manager.config_path], 'AI Command metadata migration')
            write_json(manager.config_path, result)
            write_json(manager.state / 'last-migration.json',
                       {'at': now(), 'steps': steps, 'backup': str(backup)})
        manager.config = config if dry_run else result
        return steps

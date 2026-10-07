"""Repair Claude's authenticated-first-run preference mismatch, never auth/trust.

Verified against the installed 2.1.284 CLI: onboarding completion and the
project TrustDialog are separate gates. `auth login` can omit these preferences.
Only these two non-credential keys are changed after official identity checks.
"""
import json
import os
from pathlib import Path
import re
import subprocess

from .core import ManagerError, backup_files, write_json
from .providers import executable, version


VERIFIED_VERSIONS = {'2.1.284'}


def repair_onboarding(manager, account, auth=None):
    if account['provider'] != 'claude' or not manager.has_auth(account):
        return False
    path = manager.home / '.claude.json' if account.get('legacy') else Path(account['home']) / '.claude.json'
    with manager.lock('claude-preferences-' + account['account']):
        if path.is_symlink():
            raise ManagerError('Claude: no se modifica un archivo de preferencias enlazado')
        if path.exists() and (not path.is_file() or path.stat().st_uid != os.getuid()):
            raise ManagerError('Claude: propietario/tipo de preferencias inesperado')
        content = path.read_bytes() if path.exists() else None
        try:
            data = json.loads(content) if content is not None else {}
        except (ValueError, UnicodeError):
            raise ManagerError('Claude: preferencias ilegibles; se conservan sin cambios') from None
        if not isinstance(data, dict):
            raise ManagerError('Claude: formato de preferencias desconocido; se conserva')
        if data.get('hasCompletedOnboarding') is True:
            return False
        if auth is None:
            result = subprocess.run([executable('claude'), 'auth', 'status', '--json'],
                                    capture_output=True, text=True, timeout=12,
                                    env=manager.env(account), cwd=manager.state)
            try:
                auth = json.loads(result.stdout) if result.returncode == 0 else {}
            except ValueError:
                auth = {}
        observed = auth.get('email')
        expected = account.get('email')
        if (auth.get('loggedIn') is not True or auth.get('authMethod') != 'claude.ai'
                or not isinstance(observed, str) or not expected or observed.lower() != expected.lower()):
            raise ManagerError('Claude: se necesita el login oficial verificado del correo registrado para reparar el primer inicio')
        match = re.search(r'\b(\d+\.\d+\.\d+)\b', version('claude'))
        if not match or match[1] not in VERIFIED_VERSIONS:
            raise ManagerError('Claude: reparación del primer inicio no validada para esta versión; no se cambia la configuración')
        backup_files(manager.home, [path], 'Repair Claude authenticated onboarding preferences; preserve auth and project trust')
        # Another Claude process may have saved preferences during the backup.
        if (path.read_bytes() if path.exists() else None) != content:
            raise ManagerError('Claude guardó preferencias simultáneamente; repite la operación')
        data.update(hasCompletedOnboarding=True, lastOnboardingVersion=match[1])
        write_json(path, data)
        return True

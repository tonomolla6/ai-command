"""Choose a verified account at invocation; never rotate a live session."""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import math
import os
import re

from .core import ManagerError
from .providers import query_limits


def account_override(manager, provider):
    """Resolve an invocation-only choice without consulting or rotating quotas."""
    value = os.environ.get('ACCOUNT', '').strip()
    if not value:
        return None
    match = re.fullmatch(r'(codex|claude|x|c)([1-9][0-9]*)', value, re.I)
    if match:
        selected_provider = 'codex' if match[1].lower() in ('codex', 'x') else 'claude'
        if selected_provider != provider:
            raise ManagerError('ACCOUNT pertenece a otro proveedor; usa codexN/xN o claudeN/cN según el comando')
        value = match[2]
    elif not (re.fullmatch(r'[1-9][0-9]*', value) or
              re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', value)):
        raise ManagerError('ACCOUNT debe ser codexN/xN, claudeN/cN, un número o un correo registrado')
    return manager.account(provider, value)


def quota_score(entry):
    """Conservative headroom across every reported window, excluding credits."""
    if entry.get('status') != 'OK' or not entry.get('windows'):
        return None
    values = [w.get('available_percent') for w in entry['windows']]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
           not math.isfinite(v) or not 0 < v <= 100 for v in values):
        return None
    return min(values)


def quota_expiry(entry):
    """Next known reset across published windows; never invent a missing date."""
    try:
        reference = dt.datetime.fromisoformat(entry['queried_at'])
        if reference.tzinfo is None: return None
    except (KeyError, ValueError, TypeError): return None
    resets = []
    for window in entry.get('windows', []):
        try:
            reset = dt.datetime.fromisoformat(window['reset_at'])
            if reset.tzinfo and reset > reference:
                resets.append(reset.timestamp())
        except (KeyError, ValueError, TypeError): pass
    return min(resets) if resets else None


def select_account(manager, provider, entries):
    candidates = []
    for account in manager.accounts(provider):
        if account.get('automatic') is False:continue
        if not manager.has_auth(account):
            continue
        entry = entries.get(manager.key(account), {})
        expected = account.get('email')
        observed = entry.get('email')
        if not (expected and observed and entry.get('email_verified') and
                expected.lower() == observed.lower()):
            continue
        score = quota_score(entry)
        if score is not None:
            expiry = quota_expiry(entry)
            priority = 1 if account.get('priority') == 'low' else 0
            candidates.append((priority, expiry if expiry is not None else math.inf,
                               -score, int(account['account']), account))
    if not candidates:
        raise ManagerError(f"No hay cuentas {provider} con identidad y cuota disponibles verificadas. "
                           f"Consulta ai usage; puedes elegir explícitamente una cuenta con ai {provider} <número>.")
    # Low priority is a last resort. Within each tier, use the nearest reset,
    # then headroom and account number; all tiers require verified quota.
    candidates.sort(key=lambda pair: pair[:4])
    return candidates[0][4]


def refresh_accounts(manager, provider):
    """One fresh official read per authenticated active account; no stale fallback."""
    accounts = [a for a in manager.accounts(provider) if a.get('automatic') is not False and manager.has_auth(a)]
    entries = collect_limits(manager, accounts)
    previous = manager.cache().get('accounts', {})
    for key, entry in entries.items():
        if entry['status'] == 'UNKNOWN' and previous.get(key, {}).get('windows'):
            old = previous[key]
            entry['previous_success'] = {'queried_at': old.get('queried_at'), 'windows': old['windows']}
    manager.save_limits(entries)
    return entries


def collect_limits(manager, accounts):
    """Start all current profiles together; cap future growth to eight probes."""
    if not accounts: return {}
    with ThreadPoolExecutor(max_workers=min(8,len(accounts))) as pool:
        return dict(pool.map(lambda a: (manager.key(a),query_limits(manager,a)),accounts))

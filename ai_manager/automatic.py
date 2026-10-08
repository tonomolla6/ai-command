"""Choose a verified account at invocation; never rotate a live session."""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import math
import os
import re

from .core import ManagerError
from .providers import query_limits


QUOTA_CACHE_TTL = 120


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


def quota_cache_current(account, entry, reference):
    """Reuse bounded official observations, never invent availability at a reset."""
    if not isinstance(entry, dict):return False
    if entry.get('provider') != account['provider'] or str(entry.get('account')) != str(account['account']):
        return False
    expected = account.get('email')
    observed = entry.get('email')
    if not (isinstance(expected, str) and isinstance(observed, str) and expected.lower() == observed.lower()):
        return False
    if entry.get('status') not in ('OK', 'UNKNOWN'):
        # An account that has just logged in must not inherit SIN LOGIN.
        return False
    try:
        queried = dt.datetime.fromisoformat(entry['queried_at'])
        if queried.tzinfo is None:return False
        age = (reference - queried).total_seconds()
        if not 0 <= age < QUOTA_CACHE_TTL:return False
    except (KeyError, TypeError, ValueError):return False
    windows = entry.get('windows', [])
    if not isinstance(windows, list):return False
    for window in windows:
        if not isinstance(window, dict):return False
        try:
            reset = dt.datetime.fromisoformat(window['reset_at'])
            if reset.tzinfo is not None and queried < reset <= reference:return False
        except (KeyError, TypeError, ValueError):pass
    return True


def refresh_accounts(manager, provider, force=False, notify=None):
    """Reuse recent usage; one process refreshes each provider's automatic pool."""
    accounts = [a for a in manager.accounts(provider) if a.get('automatic') is not False and manager.has_auth(a)]

    def read_cache():
        previous = manager.cache().get('accounts', {})
        reference = dt.datetime.now(dt.timezone.utc)
        pending = [a for a in accounts if force or not quota_cache_current(a, previous.get(manager.key(a)), reference)]
        return previous, pending

    previous, pending = read_cache()
    if not pending:
        if notify:notify(True)
        return {manager.key(a):previous[manager.key(a)] for a in accounts}
    # Separate from the account/probe and cache-write locks. Waiters reread the
    # result written by the first launcher instead of repeating its CLI calls.
    with manager.lock('auto-quotas-' + provider, timeout=90):
        previous, pending = read_cache()
        if notify:notify(not pending)
        if pending:
            entries = collect_limits(manager, pending)
            for key, entry in entries.items():
                if entry['status'] == 'UNKNOWN' and previous.get(key, {}).get('windows'):
                    old = previous[key]
                    entry['previous_success'] = {'queried_at': old.get('queried_at'), 'windows': old['windows']}
            manager.save_limits(entries)
            previous.update(entries)
        return {manager.key(a):previous[manager.key(a)] for a in accounts}


def collect_limits(manager, accounts):
    """Start all current profiles together; cap future growth to eight probes."""
    if not accounts: return {}
    with ThreadPoolExecutor(max_workers=min(8,len(accounts))) as pool:
        return dict(pool.map(lambda a: (manager.key(a),query_limits(manager,a)),accounts))

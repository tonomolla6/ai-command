"""Estimate the next usable account from official quota observations only."""
import datetime as dt
import math


def instant(value):
    if not isinstance(value, str):return None
    try:
        result=dt.datetime.fromisoformat(value)
        return result if result.tzinfo is not None else None
    except ValueError:return None


def countdown(reset, reference=None):
    reference=reference or dt.datetime.now(dt.timezone.utc)
    seconds=max(0,math.ceil((reset-reference).total_seconds()))
    days,seconds=divmod(seconds,86400)
    hours,seconds=divmod(seconds,3600)
    minutes,seconds=divmod(seconds,60)
    return (f'{days}d ' if days else '')+f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def _recovery(windows, queried, reference):
    if not isinstance(windows,list) or not windows:return {'status':'unknown'}
    resets=[]
    for window in windows:
        if not isinstance(window,dict):return {'status':'unknown'}
        value=window.get('available_percent')
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=100:
            return {'status':'unknown'}
        if value==0:
            reset=instant(window.get('reset_at'))
            if reset is None or reset<=queried:return {'status':'unknown'}
            resets.append(reset)
    if not resets:
        # A passed reset invalidates an old observation; it does not prove a
        # new allowance. Leave the existing query cadence in charge of refresh.
        if any((at:=instant(w.get('reset_at'))) and queried<at<=reference for w in windows):
            return {'status':'due'}
        return {'status':'available'}
    reset=max(resets)
    return {'status':'waiting' if reset>reference else 'due','reset_at':reset}


def next_availability(accounts, rows, provider, reference=None):
    reference=reference or dt.datetime.now(dt.timezone.utc)
    if provider=='opencode':return {'status':'unknown'}
    results=[]
    for account,row in zip(accounts,rows):
        if account['provider']!=provider or account.get('enabled') is False:continue
        if provider in ('codex','claude') and account.get('automatic') is False:continue
        identity=(isinstance(account.get('email'),str) and isinstance(row.get('email'),str)
                  and account['email'].lower()==row['email'].lower() and row.get('email_verified') is True)
        queried=instant(row.get('queried_at'))
        if (row.get('status')!='OK' or row.get('provider')!=provider or
                str(row.get('account'))!=str(account['account']) or not queried or queried>reference or
                (provider in ('codex','claude') and not identity)):
            results.append({'status':'unknown'});continue
        windows=row.get('windows',[])
        groups={None:windows}
        if provider=='agy' and isinstance(windows,list) and windows:
            groups={}
            for window in windows:
                name=window.get('name') if isinstance(window,dict) else None
                if not isinstance(name,str):groups={None:[]};break
                groups.setdefault(name.rsplit(' · ',1)[0],[]).append(window)
        for family,members in groups.items():
            result=_recovery(members,queried,reference)
            result.update(account=account['account'],family=family)
            results.append(result)
    available=[r for r in results if r['status']=='available']
    if available:return dict(available[0],accounts=[r['account'] for r in available])
    due=[r for r in results if r['status']=='due']
    if due:return due[0]
    waiting=[r for r in results if r['status']=='waiting']
    if waiting:
        result=min(waiting,key=lambda r:(r['reset_at'],str(r['account'])))
        return dict(result,incomplete=any(r['status']=='unknown' for r in results))
    return {'status':'unknown'}

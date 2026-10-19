"""Receive documented statusline rate_limits; never submit a model request.

https://code.claude.com/docs/en/statusline#available-data
Only quota rows, a hashed process/session key and API duration are retained.
An initial render establishes a baseline; only a later API response dates usage.
"""
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

from .core import Manager, ManagerError, atomic_write, backup_files, private_dir, read_json, write_json

TTL=120
MAX_INPUT=1024*1024


def _instant(value):
    try:
        date=dt.datetime.fromisoformat(value)
        return date if date.tzinfo is not None else None
    except (ValueError,TypeError):return None


def _number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def _identity(manager,account):
    if (account.get('provider')!='claude' or account.get('enabled') is False or
            not account.get('email_verified') or not account.get('email') or not manager.has_auth(account)):
        return False
    prefs=manager.home/'.claude.json' if account.get('legacy') else Path(account['home'])/'.claude.json'
    data=read_json(prefs)
    oauth=(data.get('oauthAccount') or {}) if isinstance(data,dict) else {}
    return isinstance(oauth,dict) and str(oauth.get('emailAddress','')).lower()==account['email'].lower()


def observe(manager,account,payload,instance,reference=None):
    reference=reference or dt.datetime.now(dt.timezone.utc)
    if not _identity(manager,account) or not isinstance(payload,dict):return False
    session=payload.get('session_id');cost=payload.get('cost') or {}
    api_ms=cost.get('total_api_duration_ms') if isinstance(cost,dict) else None
    cache=payload.get('prompt_cache') or {}
    requests=cache.get('requests') if isinstance(cache,dict) else None
    if type(requests) is not int or requests<0:requests=None
    if not isinstance(session,str) or not session or len(session)>256 or not instance:return False
    if not _number(api_ms) or api_ms<0:return False
    key=manager.key(account)
    session_key=hashlib.sha256((key+'\0'+instance+'\0'+session).encode()).hexdigest()
    private_dir(manager.state)
    with manager.lock('claude-live',timeout=.5):
        state=read_json(manager.state/'claude-live.json',{'schema':1,'accounts':{},'sessions':{}})
        sessions=state.setdefault('sessions',{});previous=sessions.get(session_key)
        # A permission toggle, timer, resume or settings reload is not a reply.
        if previous and (api_ms==previous.get('api_ms') or
                         requests is not None and requests==previous.get('requests')):return False
        sessions[session_key]={'api_ms':api_ms,'requests':requests,'seen_at':reference.isoformat()}
        if len(sessions)>256:
            state['sessions']=dict(sorted(sessions.items(),key=lambda pair:pair[1]['seen_at'])[-256:])
        windows=[];rates=payload.get('rate_limits') or {}
        if isinstance(rates,dict):
            for field,label,minutes in [('five_hour','Current session',300),('seven_day','Current week (all models)',10080)]:
                value=rates.get(field)
                if not isinstance(value,dict):continue
                used=value.get('used_percentage');reset=value.get('resets_at')
                if not _number(used) or not 0<=used<=100 or not _number(reset):continue
                try:date=dt.datetime.fromtimestamp(reset,dt.timezone.utc)
                except (ValueError,OverflowError,OSError):continue
                if date<=reference:continue
                windows.append({'name':label,'available_percent':round(100-used,1),
                                'window_minutes':minutes,'reset_at':date.isoformat()})
        # Both general windows are needed: a positive 5h allowance alone says
        # nothing about a potentially exhausted overall weekly allowance.
        updated=bool(previous and api_ms>previous.get('api_ms',api_ms) and len(windows)==2)
        if previous and requests is not None and previous.get('requests') is not None:
            updated=updated and requests>previous['requests']
        if updated:
            state.setdefault('accounts',{})[key]={
                'provider':'claude','account':account['account'],'email':account['email'],
                'email_verified':True,'observed_at':reference.isoformat(),'windows':windows}
        write_json(manager.state/'claude-live.json',state)
    if updated:manager.save_limits({})  # Atomic shared-cache change wakes monitoring.
    return updated


def merge_entry(manager,account,entry,reference=None,observations=None):
    reference=reference or dt.datetime.now(dt.timezone.utc)
    if entry.get('status')=='SIN LOGIN' or entry.get('email_verified') is False or not _identity(manager,account):return entry
    if observations is None:observations=read_json(manager.state/'claude-live.json').get('accounts',{})
    live=observations.get(manager.key(account),{})
    observed=_instant(live.get('observed_at'));queried=_instant(entry.get('queried_at'))
    if (not observed or not 0<=(reference-observed).total_seconds()<TTL or
            str(live.get('account'))!=str(account['account']) or live.get('provider')!='claude' or
            live.get('email')!=account.get('email') or live.get('email_verified') is not True):return entry
    if entry.get('status')=='OK' and entry.get('source_type')!='claude_statusline' and queried and queried>=observed:return entry
    windows=live.get('windows',[])
    if len(windows)!=2:return entry
    for window in windows:
        reset=_instant(window.get('reset_at'));value=window.get('available_percent')
        if not reset or reset<=reference or not _number(value) or not 0<=value<=100:return entry
    windows=[dict(window) for window in windows]
    recent=queried and 0<=(reference-queried).total_seconds()<TTL
    if entry.get('status')=='OK' and recent:
        for window in entry.get('windows',[]):
            if window.get('name') in {v['name'] for v in windows}:continue
            stamp=_instant(window.get('observed_at')) or (queried if entry.get('source_type')!='claude_statusline' else None)
            if stamp and 0<=(reference-stamp).total_seconds()<TTL:
                windows.append(dict(window,observed_at=stamp.isoformat()))
    if any(not _number(w.get('available_percent')) for w in windows):return entry
    credit_stamp=_instant(entry.get('credits_observed_at')) or (queried if entry.get('source_type')!='claude_statusline' else None)
    credits_current=credit_stamp and 0<=(reference-credit_stamp).total_seconds()<TTL
    result={k:live[k] for k in ('provider','account','email','email_verified','observed_at')}
    result.update(status='OK',windows=windows,available_percent=min(w['available_percent'] for w in windows),
                  queried_at=observed.isoformat(),last_success_at=observed.isoformat(),
                  source_type='claude_statusline',source='claude statusline (respuestas normales, sin consultas)',
                  credits=entry.get('credits') if credits_current else None)
    if credits_current and entry.get('credits') is not None:result['credits_observed_at']=credit_stamp.isoformat()
    attempts=[at for at in (queried,_instant(entry.get('last_attempt_at'))) if at and at>observed]
    if attempts:result['last_attempt_at']=max(attempts).isoformat()
    return result


def overlay_cache(manager,data):
    observations=read_json(manager.state/'claude-live.json').get('accounts',{})
    if not observations:return data
    rows=data.setdefault('accounts',{})
    for account in manager.accounts('claude'):
        key=manager.key(account)
        rows[key]=merge_entry(manager,account,rows.get(key,{}),observations=observations)
    return data


def configure(manager,enabled):
    """Back up and wrap only statusLine; preserve auth, permissions and output."""
    private_dir(manager.state)
    with manager.lock('claude-live-settings'):
        registry_path=manager.state/'claude-live-settings.json'
        registry=read_json(registry_path,{'bindings':{}})
        bindings=registry.setdefault('bindings',{})
        helper=manager.state/'claude-live-statusline.py'
        command=shlex.join([sys.executable,str(helper),'--observe-v1'])
        changes=[]
        for account in manager.accounts('claude'):
            path=Path(account['home'])/'settings.json'
            if path.is_symlink() or not path.resolve().is_relative_to(manager.home.resolve()):
                raise ManagerError('Claude live: settings enlazados/externos se conservan sin modificar')
            if path.exists() and (not path.is_file() or path.stat().st_uid!=os.getuid()):
                raise ManagerError('Claude live: propietario/tipo de settings inesperado')
            raw=path.read_bytes() if path.exists() else None
            try:data=json.loads(raw) if raw is not None else {}
            except (ValueError,UnicodeError):raise ManagerError('Claude live: settings ilegibles; no se modifican') from None
            if not isinstance(data,dict):raise ManagerError('Claude live: formato de settings desconocido')
            current=data.get('statusLine');key=str(path)
            if current is not None and (not isinstance(current,dict) or current.get('type')!='command' or not isinstance(current.get('command'),str)):
                raise ManagerError('Claude live: statusLine incompatible; se conserva')
            if enabled:
                managed=current and current.get('command') in (command,registry.get('command'))
                if managed:
                    if key not in bindings:raise ManagerError('Claude live: falta el estado de restauración')
                    if current.get('command')==command:continue
                else:bindings[key]={'original':current}
                data['statusLine']=dict(current or {},type='command',command=command)
            elif current and current.get('command') in (command,registry.get('command')) and key in bindings:
                original=bindings[key]['original']
                if original is None:data.pop('statusLine')
                else:data['statusLine']=original
            else:continue
            changes.append((path,raw,data))
        if changes:
            backup_files(manager.home,[p for p in [*(c[0] for c in changes),registry_path,helper] if p.exists()],
                         'Configure official Claude live quota statusline; preserve credentials and permissions')
        if enabled:
            from .distribution import installation
            try:root=installation()/'current'
            except ManagerError:root=Path(__file__).resolve().parent.parent
            text='#!'+sys.executable+'\nimport sys\nsys.path.insert(0, '+repr(str(root))+')\nfrom ai_manager.claude_live import main\nraise SystemExit(main())\n'
            if not helper.exists() or helper.read_text()!=text:atomic_write(helper,text,0o700)
        registry.update(enabled=enabled,command=command);write_json(registry_path,registry)
        for path,raw,data in changes:
            if (path.read_bytes() if path.exists() else None)!=raw:
                raise ManagerError('Claude guardó settings simultáneamente; repite la configuración')
            if not path.parent.exists():private_dir(path.parent)
            write_json(path,data)
    return len(changes)


def ensure_account(manager,account):
    registry=read_json(manager.state/'claude-live-settings.json')
    if registry.get('enabled') and str(Path(account['home'])/'settings.json') not in registry.get('bindings',{}):
        configure(manager,True)


def _instance(proc=None,parent=None):
    """Native ancestor identity is stable across the statusline's short shells."""
    if os.environ.get('AI_MANAGER_CLAUDE_RUN'):return os.environ['AI_MANAGER_CLAUDE_RUN']
    proc=Path('/proc') if proc is None else Path(proc)
    pid=os.getppid() if parent is None else parent
    for _ in range(8):
        try:
            root=proc/str(pid);fields=(root/'stat').read_text().rsplit(')',1)[1].split()
            name=Path(os.readlink(root/'exe').removesuffix(' (deleted)')).name
            native=name in ('claude','claude.exe') or (re.fullmatch(r'\d+\.\d+\.\d+',name) and
                                                       (root/'comm').read_text().strip() in ('claude','claude.exe'))
            if native:return str(pid)+':'+fields[19]
            pid=int(fields[1])
        except (OSError,ValueError,IndexError):break
    return None


def main():
    # Stdout belongs to the existing status line. Never log the payload/errors.
    raw=sys.stdin.buffer.read(MAX_INPUT+1)
    if len(raw)>MAX_INPUT:return 0
    manager=Manager();profile=Path(os.environ.get('CLAUDE_CONFIG_DIR') or manager.home/'.claude').resolve()
    account=next((a for a in manager.accounts('claude') if Path(a['home']).resolve()==profile),None)
    registry=read_json(manager.state/'claude-live-settings.json')
    binding=registry.get('bindings',{}).get(str(profile/'settings.json'),{})
    if registry.get('enabled') and account:
        try:observe(manager,account,json.loads(raw),_instance())
        except (ManagerError,OSError,ValueError,TypeError,AttributeError):pass
    original=binding.get('original') or {}
    if original.get('command'):
        try:subprocess.run(original['command'],shell=True,input=raw,timeout=2)
        except (OSError,subprocess.SubprocessError):pass
    return 0


if __name__=='__main__':raise SystemExit(main())

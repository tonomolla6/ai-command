"""Existing single-account tools. No credential migration or private endpoints."""
import json
from pathlib import Path
import re
import subprocess
import sys

from .core import ManagerError, now, read_json, write_json
from .handoff import handoff_prompt
from .providers import executable, reset_value
from .sessions import same_directory
from .ui import colored


SINGLE_TOOLS = {'agy': ('Antigravity', '.gemini/antigravity-cli'),
                'opencode': ('OpenCode', '.local/share/opencode')}


def current_tools(manager):
    result=[]
    for provider,(label,relative) in SINGLE_TOOLS.items():
        try: executable(provider)
        except ManagerError: continue
        result.append({'provider':provider,'account':'current','label':label,
                       'home':str(manager.home/relative),'single':True})
    return result


def current_status(manager,account):
    if account['provider']=='opencode':
        ready=bool(read_json(manager.home/'.local/share/opencode/auth.json'))
    else:
        ready=manager.cache().get('accounts',{}).get('agy:current',{}).get('status')=='OK'
        ready=ready or (manager.home/'.gemini/antigravity-cli/antigravity-oauth-token').is_file()
    return ready,'CUENTA ACTUAL' if ready else 'UNKNOWN'


def run_read(provider,args,cwd,timeout=25):
    result=subprocess.run([executable(provider),*args],cwd=cwd,stdin=subprocess.DEVNULL,
                          capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        raise ManagerError(provider+': la consulta oficial falló; salida privada omitida')
    return result.stdout


def parse_opencode_models(text):
    result=[]
    for match in re.finditer(r'(?m)^(opencode/[^\s]+)\n',text):
        try: data,_=json.JSONDecoder().raw_decode(text[match.end():])
        except ValueError: continue
        if not isinstance(data,dict):continue
        cost=data.get('cost',{})
        if not isinstance(cost,dict):continue
        cache=cost.get('cache',{})
        if not isinstance(cache,dict):continue
        costs=[cost.get('input'),cost.get('output'),cache.get('read',0),cache.get('write',0)]
        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and v==0 for v in costs):continue
        if data.get('status')!='active' or data.get('capabilities',{}).get('toolcall') is not True:continue
        # Persist only public catalog metadata, never headers, options or keys.
        result.append({'id':match[1],'name':data.get('name',data.get('id')),
                       'context':data.get('limit',{}).get('context'),
                       'release_date':data.get('release_date'),'input_cost':0,'output_cost':0})
    return sorted(result,key=lambda v:(v.get('release_date') or '',v['id']),reverse=True)


def models(manager,provider,refresh=False):
    path=manager.state/('models-'+provider+'.json')
    cached=read_json(path)
    # Launches explicitly request a fresh catalog; display may use a short cache.
    if not refresh and cached.get('models'):
        import datetime as dt
        try:
            age=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(cached['queried_at'])).total_seconds()
            if 0<=age<120:return cached['models']
        except (ValueError,KeyError,TypeError):pass
    if provider=='opencode':
        result=parse_opencode_models(run_read(provider,['--pure','models','opencode','--verbose','--refresh'],manager.state))
    else:
        result=[]
        for line in run_read(provider,['models'],manager.state).splitlines():
            parts=line.split('\t',1)
            if len(parts)==2 and re.fullmatch(r'[A-Za-z0-9._-]+',parts[0]):
                result.append({'id':parts[0],'name':parts[1],'cost':'UNKNOWN'})
    if not result:raise ManagerError('No hay modelos reconocibles en el catálogo oficial de '+provider)
    write_json(path,{'queried_at':now(),'models':result})
    return result


def parse_agy_usage(text):
    windows=[]
    for line in text.splitlines():
        fields=line.split('\t')
        if len(fields)!=4:continue
        group,label,percentage,date=fields
        match=re.fullmatch(r'(\d+(?:\.\d+)?)%',percentage.strip())
        if not match or 'remaining' not in label.lower():continue
        value=float(match[1])
        if not 0<=value<=100:continue
        minutes=10080 if 'weekly' in label.lower() else 300 if 'five hour' in label.lower() else None
        windows.append({'name':group+' · '+('Semanal' if minutes==10080 else '5h' if minutes==300 else label),
                        'available_percent':value,'window_minutes':minutes,'reset_at':reset_value(date.strip())})
    return windows


def parse_agy_credits(text):
    for line in text.splitlines():
        fields=line.split('\t')
        if len(fields)==2 and fields[0].strip().lower()=='remaining credits':
            value=fields[1].strip()
            if re.fullmatch(r'\d+(?:\.\d+)?',value):
                return {'balance':value,'unit':'provider credits','display':value+' créditos','source':'agy -p /credits'}
    return None


def group_agy_models(catalog):
    groups={}
    for model in catalog:
        name=model['name'];match=re.fullmatch(r'(.*?)\s*\((Low|Medium|High)\)',name,re.I)
        base=match[1] if match else name
        group=groups.setdefault(base,{'name':base,'ids':[],'efforts':[]})
        group['ids'].append(model['id'])
        if match and match[2].lower() not in group['efforts']:group['efforts'].append(match[2].lower())
    for group in groups.values():
        group['efforts']=sorted(group['efforts'],key=('low','medium','high').index)
    return list(groups.values())


def query_usage(manager,account):
    provider=account['provider']
    entry={'provider':provider,'account':'current','queried_at':now(),'email':None,'email_verified':False,
           'windows':[],'available_percent':None,'credits':None,'status':'UNKNOWN'}
    try:
        with manager.lock('probe-'+provider+'-current',timeout=15):
            if provider=='agy':
                windows=parse_agy_usage(run_read(provider,['-p','/usage'],manager.state))
                if not windows:raise ManagerError('Antigravity /usage: formato no reconocido')
                entry.update(windows=windows,available_percent=min(w['available_percent'] for w in windows),
                             status='OK',source='agy -p /usage (comando interno sin turnos)')
                # Built-in read commands, not model prompts. Failures in these
                # optional details must not discard a valid quota response.
                try:entry['credits']=parse_agy_credits(run_read(provider,['-p','/credits'],manager.state))
                except (ManagerError,OSError,subprocess.SubprocessError):entry['credits_reason']='UNKNOWN: consulta oficial de créditos no disponible'
                try:entry['models']=group_agy_models(models(manager,provider,refresh=True))
                except (ManagerError,OSError,ValueError,subprocess.SubprocessError):entry['models_reason']='Catálogo de modelos UNKNOWN'
            else:
                try:entry['activity']=local_activity(manager)
                except (ManagerError,OSError,ValueError,subprocess.SubprocessError):entry['activity_reason']='Actividad local UNKNOWN'
                catalog=models(manager,provider,refresh=True)
                entry.update(free_models=catalog,selected_model=catalog[0]['id'],
                             source='opencode --pure models opencode --verbose',
                             reason='Cuotas y saldo: UNKNOWN; catálogo gratuito verificado')
    except (ManagerError,OSError,ValueError,subprocess.SubprocessError) as exc:
        entry['reason']=str(exc) if isinstance(exc,ManagerError) else type(exc).__name__
    return entry


def local_activity(manager):
    from .opencode_history import read_history
    path=run_read('opencode',['--pure','db','path'],manager.state).strip()
    if not path or '\n' in path or not Path(path).is_absolute():
        raise ManagerError('OpenCode: ruta de base local no reconocida')
    return read_history(path)


def list_tool_sessions(manager,provider,cwd):
    if provider=='agy':
        mapping=read_json(manager.home/'.gemini/antigravity-cli/cache/last_conversations.json')
        if not isinstance(mapping,dict):raise ManagerError('Antigravity: formato de índice de conversaciones desconocido')
        return [{'id':ident,'directory':directory,'updated':0} for directory,ident in mapping.items()
                if isinstance(ident,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,150}',ident) and same_directory(directory,cwd)]
    data=json.loads(run_read(provider,['--pure','session','list','--format','json','--max-count','300'],cwd))
    if not isinstance(data,list):raise ManagerError('OpenCode: formato de sesiones no reconocido')
    result=[]
    for row in data:
        if not isinstance(row,dict):continue
        ident=row.get('id');directory=row.get('directory')
        if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',ident):continue
        if isinstance(directory,str) and same_directory(directory,cwd):
            try:updated=float(row.get('updated') or 0)
            except (ValueError,TypeError):continue
            result.append({'id':ident,'directory':directory,'updated':updated})
    return sorted(result,key=lambda r:r['updated'],reverse=True)


def launch_tool(manager,args,resume=False):
    provider=args.provider
    if not args.dry_run and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise ManagerError('Abrir una sesión necesita TTY; usa --dry-run para inspeccionar')
    command=[executable(provider)]
    session=None
    if resume:
        sessions=list_tool_sessions(manager,provider,Path.cwd())
        if not sessions:raise ManagerError('No se encontró una sesión de '+provider+' en este directorio. Abre una nueva con ai '+provider+'.')
        session=sessions[0]
        if getattr(args,'pick',False):
            from .cli import choose
            session=choose('Sesiones del directorio actual',[(row,row['id']) for row in sessions])
            if session is None:return 0
    extras=list(args.extra or [])
    if extras[:1]==['--']:extras=extras[1:]
    if provider=='opencode':
        catalog=models(manager,provider,refresh=True)
        selected=args.model or catalog[0]['id']
        if selected not in {m['id'] for m in catalog}:
            raise ManagerError('Ese modelo no figura con tarifa cero en el catálogo actual. Usa ai models opencode.')
        if any(v=='--model' or v.startswith('--model=') or v.startswith('-m') for v in extras):
            raise ManagerError('Usa ai opencode --model <modelo-free> para verificar su tarifa')
        command.extend(['--model',selected])
    elif args.model:
        if args.model not in {m['id'] for m in models(manager,provider,refresh=True)}:
            raise ManagerError('Modelo ausente del catálogo actual de Antigravity')
        command.extend(['--model',args.model])
    if session:command.extend(['--session' if provider=='opencode' else '--conversation',session['id']])
    prompt=handoff_prompt(manager,Path.cwd())
    if prompt:command.extend(['--prompt' if provider=='opencode' else '--prompt-interactive',prompt])
    command.extend(extras)
    if args.dry_run:
        print(json.dumps({'command':command,'cwd':str(Path.cwd()),'account':'current',
                          'credentials':'existing installation, unchanged'},indent=2));return 0
    print(colored(SINGLE_TOOLS[provider][0],provider)+' · cuenta actual · '+str(Path.cwd()),flush=True)
    manager.record_launch({'provider':provider,'account':'current'},Path.cwd())
    result=subprocess.run(command,cwd=Path.cwd())
    return result.returncode if result.returncode>=0 else 128-result.returncode

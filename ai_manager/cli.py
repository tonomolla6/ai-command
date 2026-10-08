"""Human-oriented commands; selection is explicit or automatic at invocation."""
from __future__ import annotations

import argparse
import contextlib
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

from .core import Manager, ManagerError, now, public_text, safe_file, read_json, backup_files
from .automatic import refresh_accounts, select_account, quota_score, quota_expiry, collect_limits, account_override
from .handoff import handoff, handoff_prompt
from .providers import AUTO_WRAPPER_MARKER, auth_status, executable, version
from .sessions import list_sessions, prepare_resume, session_lock_name
from .setup import install_commands, install_auto_commands, setup
from .registry import add_account, rename_account, save_registry, sync_identity, set_codex_policy, fixed_model_arguments, set_priority
from .ui import colored, render_usage, activity_lines, compact_tokens
from .single_tools import SINGLE_TOOLS, current_tools, launch_tool, models, list_tool_sessions, local_activity
from .claude_setup import repair_onboarding
from .permissions import claude_danger_enabled, claude_arguments, codex_arguments
from .migrations import migrate, CURRENT
from .core import private_dir, write_json
from . import __version__


def quota_state(value):
    if value is None:return "UNKNOWN"
    return "AGOTADO" if value<=0 else ("BAJO" if value<25 else "OK")


def pct(value):
    return f"{value:g}%" if isinstance(value,(int,float)) else "UNKNOWN"


def cached_age(entry):
    try:return max(0,int(time.time()-dt.datetime.fromisoformat(entry["queried_at"]).timestamp()))
    except (KeyError,ValueError,TypeError):return None


def format_reset(window):
    if window.get('reset_display'):return window['reset_display']
    value=window.get("reset_at")
    if value:
        try:
            date=dt.datetime.fromisoformat(value).astimezone()
            # Claude's relative reset report can land milliseconds before the
            # displayed minute; match its minute precision without changing data.
            if date.second>=30:date+=dt.timedelta(minutes=1)
            return date.strftime("%d/%m %H:%M %Z")
        except ValueError:pass
    return window.get("reset_display") or "UNKNOWN"


def limits(manager,args):
    if getattr(args,'monitoring',False):
        from .monitoring import monitor
        return monitor(manager,args)
    entries=manager.cache().get("accounts",{})
    provider=getattr(args,'provider',None)
    accounts=manager.accounts(provider)+[a for a in current_tools(manager) if provider is None or a['provider']==provider]
    if not args.cached:
        pending=[a for a in accounts if args.refresh or cached_age(entries.get(manager.key(a),{})) is None or
                 cached_age(entries.get(manager.key(a),{}))>=120 or
                 (not a.get('single') and manager.has_auth(a) and entries.get(manager.key(a),{}).get("reason")=="SIN LOGIN")]
        if pending:
            if not args.json: print(f"Consultando {len(pending)} perfiles en paralelo (sin generar respuestas)…",flush=True)
            updated=collect_limits(manager,pending)
            # Failure invalidates current percentages. Keep prior successful windows
            # explicitly marked as historical, never presented as fresh quota.
            for key,value in updated.items():
                old=entries.get(key,{})
                if value["status"]=="UNKNOWN" and old.get("windows"):
                    value["previous_success"]={"queried_at":old.get("queried_at"),"windows":old["windows"]}
            manager.save_limits(updated);entries.update(updated)
    rows=[entries.get(manager.key(a),{"provider":a["provider"],"account":a["account"],"status":"UNKNOWN","windows":[]}) for a in accounts]
    if args.json:print(json.dumps({"schema":1,"accounts":rows},indent=2));return 0
    render_usage(accounts,rows,format_reset,cached_age,getattr(args,'layout','auto'))
    return 0


def accounts(manager,args):
    selected=manager.accounts(include_inactive=getattr(args,'all',False))+current_tools(manager)
    with ThreadPoolExecutor(max_workers=3) as pool:
        statuses=list(pool.map(lambda a:auth_status(manager,a) if a.get('enabled',True) else (False,'DE BAJA'),selected))
    rows=[]
    for a,(ready,status) in zip(selected,statuses):
        rows.append({"provider":a["provider"],"account":a["account"],"email":a.get('email'),"home":a["home"],"configured":ready,"status":status,
                     "fixed_model":a.get('fixed_model'),"automatic":a.get('automatic',True),"plan_label":a.get('plan_label'),
                     "priority":a.get('priority','normal')})
    if args.json:print(json.dumps(rows,indent=2));return 0
    print(colored('AI ACCOUNTS','title'))
    for a,row in zip(selected,rows):
        print(colored(f"{a['label']:<12}",a['provider'])+' '+colored(f"{row['status']:<14}",'ok' if row['configured'] else 'low')+' '+colored(a.get('email') or ('correo no publicado' if a.get('single') else 'correo pendiente'),'bold'))
        print('  '+colored(a['home']+(' (perfil existente)' if a.get('legacy') else ''),'muted'))
        if a.get('fixed_model'):print('  '+colored((a.get('plan_label') or '')+' · '+a['fixed_model']+(' · manual' if a.get('automatic') is False else ''),'low'))
        if a.get('priority')=='low':print('  '+colored('↓ Prioridad baja · última opción automática','low'))
        if not row["configured"] and a.get('enabled',True) and not a.get('single'):print(f"  Login una vez: ai login {a['provider']} {a['account']}")
    return 0


def choose(title,options):
    if not sys.stdin.isatty():raise ManagerError("El menú necesita una terminal; usa un comando explícito")
    if shutil.which("fzf"):
        result=subprocess.run(["fzf","--prompt",title+" > ","--height","80%","--layout","reverse",
                               "--ansi","--no-multi","--delimiter","\t","--with-nth","2.."],
                              input="\n".join(f"{i}\t{label}" for i,(_,label) in enumerate(options)),text=True,stdout=subprocess.PIPE)
        if result.returncode:return None
        return options[int(result.stdout.split("\t",1)[0])][0]
    print("\n"+colored(title,'title'))
    for i,(_,label) in enumerate(options,1):print(f"  {i:>2}. {label}")
    print("   q. Salir")
    while True:
        value=input("Seleccionar > ").strip()
        if value.lower() in ("q",""):return None
        for result,label in options:
            if label.lower().startswith('['+value.lower()+']'):return result
        if value.isdigit() and 1<=int(value)<=len(options):return options[int(value)-1][0]
        print("Selección no válida.")


def selected_session(manager,provider,cwd,pick=False):
    sessions=list_sessions(manager,provider,cwd)
    if not sessions:raise ManagerError("No se encontró una sesión de este proveedor en el directorio actual. "
                                        "Usa ai sessions o ai handoff y abre una sesión nueva.")
    if pick:
        return choose("Sesiones del directorio actual",[(s,f"{s['id']} · cuenta {s['origin_account']} · "+
                     dt.datetime.fromtimestamp(s['updated']).astimezone().strftime('%d/%m %H:%M')) for s in sessions[:60]])
    return sessions[0]


def launch(manager,args,resume=False,session=None):
    extras=list(getattr(args,"extra",[]) or [])
    if extras[:1]==["--"]:extras=extras[1:]
    if args.provider=='claude':
        from .sessions import claude_resume_request
        requested,ident,remaining=claude_resume_request(extras)
        if requested:
            resume=True;extras=remaining
            if ident:args.session=ident
    account=manager.account(args.provider,args.account)
    if not manager.has_auth(account) and not getattr(args,'dry_run',False):
        raise ManagerError(f"{account['label']} necesita login: ai login {args.provider} {args.account}")
    if not getattr(args,'dry_run',False):
        identity=sync_identity(manager,account)
        if getattr(args,'automatic',False) and not identity:
            raise ManagerError('No se pudo verificar de nuevo la identidad antes del lanzamiento')
        if args.provider=='claude':repair_onboarding(manager,account)
    cwd=Path.cwd()
    if resume and session is None and getattr(args,'session',None):
        session=next((s for s in list_sessions(manager,args.provider,None) if s['id']==args.session),None)
        if session is None:raise ManagerError('ID de sesión no encontrado; consulta ai sessions '+args.provider+' --all')
    if resume and session is None:session=selected_session(manager,args.provider,cwd,getattr(args,"pick",False))
    if resume and session is None:return 0
    prompt=handoff_prompt(manager,cwd)
    command=[executable(args.provider)]
    if args.provider=="codex":
        command.extend(["--no-daemon","--no-alt-screen"])
        if account.get("shared_sqlite_home"):
            command.extend(["-c","sqlite_home="+json.dumps(account['shared_sqlite_home'])])
    if session:command.extend(prepare_resume(manager,account,session,dry_run=getattr(args,'dry_run',False)))
    command.extend(fixed_model_arguments(account,extras))
    if prompt:
        # Recorded Claude system prompts can ignore --append-system-prompt on
        # resume, so supply the explicit handoff instruction as a user prompt.
        if args.provider=="claude":command.append(prompt)
        else:command.extend(["-c","developer_instructions="+json.dumps(prompt)])
    command.extend(extras)
    if args.provider=='codex':command=[command[0],*codex_arguments(manager,command[1:])]
    danger = args.provider == 'claude' and claude_danger_enabled(manager)
    if danger:command=[command[0], *claude_arguments(command[1:])]
    if getattr(args,"dry_run",False):
        print(json.dumps({"command":command,"cwd":str(cwd),"home":account["home"],
                          "environment":{'IS_SANDBOX':'1'} if danger else {},
                          "provider":account['provider'],"account":account['account'],"email":account.get('email'),
                          "automatic":getattr(args,'automatic',False),
                          "next_reset_at":getattr(args,'auto_reset',None),
                          "session_id":session["id"] if session else None},indent=2));return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ManagerError("Abrir una sesión necesita TTY; para inspección usa --dry-run")
    manager.record_launch(account,cwd)
    lock=manager.lock(session_lock_name(args.provider,session["id"]),blocking=False) if session else contextlib.nullcontext()
    with lock:
        print(colored(account['label'],account['provider'])+' · '+colored(account.get('email') or 'correo pendiente','bold')+
              f" · {cwd}"+(f" · resume {session['id']}" if session else ""),flush=True)
        # Same foreground process group/terminal so Ctrl+C/Ctrl+D retain CLI semantics.
        env=manager.env(account);env.pop('ACCOUNT',None);env['AI_MANAGER_BOUND_PROVIDER']=account['provider']
        if args.provider=='claude':env['CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN']='1'
        if danger:env['IS_SANDBOX']='1'
        try:result=subprocess.run(command,env=env,cwd=cwd)
        except KeyboardInterrupt:return 130
    if resume and result.returncode:
        print("El CLI no pudo reanudar (puede ser bloqueo, incompatibilidad o restricción de cuenta). "
              f"Prueba ai resume {args.provider} {args.account} --pick, o ai handoff. No se modificaron credenciales.",file=sys.stderr)
        if sys.stdin.isatty():
            action=choose("Recuperación",[("pick","Seleccionar otra sesión"),("handoff","Crear handoff local")])
            if action=="pick":args.pick=True;return launch(manager,args,True)
            if action=="handoff":handoff(manager,cwd,{})
    return result.returncode if result.returncode>=0 else 128-result.returncode


def auto_launch(manager,args):
    if not args.dry_run and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise ManagerError('La selección automática interactiva necesita TTY; usa --dry-run para inspeccionar')
    account=account_override(manager,args.provider)
    explicit=account is not None
    if explicit:
        row={}
    else:
        print(colored(f'AI AUTO · consultando cuotas oficiales de {args.provider}…','title'),file=sys.stderr,flush=True)
        entries=refresh_accounts(manager,args.provider)
        account=select_account(manager,args.provider,entries)
        row=entries[manager.key(account)]
    expiry=quota_expiry(row)
    reset=dt.datetime.fromtimestamp(expiry,dt.timezone.utc).isoformat() if expiry is not None else None
    resume_requested=getattr(args,'resume',False) or bool(getattr(args,'session',None))
    session=None
    if resume_requested:
        if getattr(args,'session',None):
            session=next((s for s in list_sessions(manager,args.provider,None) if s['id']==args.session),None)
            if session is None:raise ManagerError('ID de conversación principal no encontrado; consulta ai sessions '+args.provider+' --all')
        else:session=selected_session(manager,args.provider,Path.cwd(),getattr(args,'pick',False))
        if session is None:return 0
    if not args.dry_run and explicit:
        print(colored('AI ACCOUNT · '+account['label'],args.provider)+' · elección explícita',file=sys.stderr,flush=True)
    elif not args.dry_run:
        print(colored('✓ '+account['label'],args.provider)+' · '+colored(row['email'],'bold')+
              ' · '+colored(pct(quota_score(row))+' de margen mínimo','ok')+
              ' · '+colored('reinicio '+format_reset({'reset_at':reset}) if reset else 'reset UNKNOWN; prioridad por margen','low')+
              ' · '+('retomando conversación' if session else 'conversación nueva'),file=sys.stderr,flush=True)
    selected=argparse.Namespace(provider=args.provider,account=account['account'],dry_run=args.dry_run,
                                pick=False,extra=args.extra,automatic=not explicit,auto_reset=reset)
    return launch(manager,selected,resume=session is not None,session=session)


def login(manager,args):
    account=manager.account(args.provider,args.account)
    if manager.has_auth(account) and not args.reauth:
        if not sync_identity(manager,account):
            raise ManagerError('No se pudo verificar el login existente. Para renovarlo: ai login '+args.provider+' '+args.account+' --reauth')
        repaired=repair_onboarding(manager,account)
        print(account['label']+' · '+account['email']+' · login verificado'+
              ('; primer inicio reparado, ya puedes abrir c'+account['account'] if repaired else '; listo para abrir'))
        return 0
    if not sys.stdin.isatty():raise ManagerError("Login requiere una terminal interactiva")
    command=([executable("codex"),"login","--device-auth"] if args.provider=="codex" else
             [executable("claude"),"auth","login","--claudeai"])
    if args.reauth:
        state=manager.home/'.claude.json' if account.get('legacy') else Path(account['home'])/'.claude.json'
        backup_files(manager.home,[manager.credential(account),state],"Explicit reauthentication backup")
    print(f"Login independiente: {account['label']}. Selecciona su cuenta en el navegador.",flush=True)
    with manager.lock("login-"+manager.key(account).replace(":","-"),blocking=False):
        code=subprocess.call(command,env=manager.env(account),cwd=Path.cwd())
        if code==0:
            if not sync_identity(manager,account):raise ManagerError('El CLI terminó, pero no se pudo verificar el correo del login')
            repair_onboarding(manager,account)
        return code


def doctor(manager,args):
    problems=[];warnings=[]
    print(colored("AI DOCTOR · diagnóstico",'title'))
    for provider in ('codex','claude','agy','opencode'):
        print(provider+' auto-approve: '+('ON' if manager.config.get(provider+'_skip_permissions',False) else 'OFF'))
    from .provider_updates import configure_cron
    print('Actualizaciones periódicas CLI: '+('ON · cada 6 horas' if configure_cron(manager,'status') else 'OFF'))
    for provider,label in (("codex","Codex CLI"),("claude","Claude Code")):
        v=version(provider);print(colored(label,provider)+': '+colored('OK' if v!='UNKNOWN' else 'ERROR','ok' if v!='UNKNOWN' else 'empty')+' · '+v)
        if v=="UNKNOWN":problems.append(label+" no disponible")
        resolved=shutil.which(provider)
        try:
            with open(resolved,'rb') as stream:auto=AUTO_WRAPPER_MARKER in stream.read(256)
        except (OSError,TypeError):auto=False
        print(f"Auto {provider}: "+colored('OK' if auto else 'not installed','ok' if auto else 'muted')+
              (f' · {resolved} (sin argumentos)' if auto else ''))
    for tool in current_tools(manager):
        value=version(tool['provider'])
        print(colored(tool['label'],tool['provider'])+': '+colored('OK' if value!='UNKNOWN' else 'ERROR','ok' if value!='UNKNOWN' else 'empty')+
              ' · '+value+' · cuenta actual, credenciales conservadas')
        if value=='UNKNOWN':problems.append(tool['label']+' no disponible')
    with ThreadPoolExecutor(max_workers=3) as pool:
        statuses=list(pool.map(lambda a:auth_status(manager,a),manager.accounts()))
    for provider in ('codex','claude'):
        total=len(manager.accounts(provider))
        count=sum(ready for a,(ready,_) in zip(manager.accounts(),statuses) if a["provider"]==provider)
        print(colored(f"{provider.title()} accounts",provider)+': '+colored(f'{count}/{total} configured','ok' if count==total else 'low'))
    paths=[]
    for a in manager.accounts():
        credential=manager.credential(a)
        if Path(a['home']).is_symlink():problems.append(a['label']+": home symlink")
        if credential.is_symlink():problems.append(a['label']+": credencial symlink")
        if credential.exists():
            st=credential.stat()
            if st.st_uid!=os.getuid() or st.st_mode&0o077:problems.append(a['label']+": permisos de credencial")
            identity=(st.st_dev,st.st_ino)
            if identity in paths:problems.append(a['label']+": credencial compartida")
            paths.append(identity)
        if not Path(a['profile']).is_dir():problems.append(a['label']+": falta descriptor de perfil")
    print("Credential isolation: "+colored("OK" if not problems else "REVISAR",'ok' if not problems else 'empty'))
    links=read_json(manager.state/"sharing-audit.json").get("files",[])
    bad_links=0
    for item in links:
        if not item['shared']:continue
        try:a=manager.account(item['provider'],item['account'],allow_inactive=True)
        except ManagerError:bad_links+=1;continue
        path=Path(a['home'])/item['relative_path']
        if not path.is_symlink() or not path.exists() or not safe_file(path.resolve(),max_bytes=2*1024*1024):bad_links+=1
    if bad_links:problems.append(f"{bad_links} enlaces de instrucciones requieren revisión")
    codex_homes={a.get('shared_sqlite_home') for a in manager.accounts('codex')}
    history_ok=(not manager.accounts('codex') or (len(codex_homes)==1 and None not in codex_homes)) and all(Path(a['home']).is_dir() for a in manager.accounts())
    if not history_ok:problems.append('Configuración de historial compartido incoherente')
    print("Shared history: "+colored("OK" if not bad_links and history_ok else "REVISAR",'ok' if not bad_links and history_ok else 'empty')+" · Codex sqlite_home; Claude resume por ruta; auth separado")
    print("fzf: "+("installed" if shutil.which('fzf') else "not installed (menú estándar disponible)"))
    cache=manager.cache().get('accounts',{})
    for a in manager.accounts()+current_tools(manager):
        item=cache.get(manager.key(a),{})
        age=cached_age(item)
        status=item.get('status','UNKNOWN')
        exhausted=status=='OK' and any(isinstance(w.get('available_percent'),(int,float)) and w['available_percent']<=0 for w in item.get('windows',[]))
        print(f"Limits {a['label']}: "+colored(status,'ok' if status=='OK' else 'low')+(" (STALE)" if age is not None and age>=120 else "")+
              (colored(' · cuota AGOTADA','empty') if exhausted else '')+
              (" · "+public_text(item['reason']) if item.get('reason') else ""))
        if a['provider']=='opencode' and item.get('activity'):
            print('  Actividad local: '+colored('OK','ok')+f" · {item['activity']['record_count']} registros · cuota real UNKNOWN")
        if status!='OK':warnings.append(a['label']+": límites UNKNOWN")
    inherited=[k for k in os.environ if k in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','OPENAI_API_KEY','CLAUDE_CONFIG_DIR','CODEX_HOME')]
    if inherited:warnings.append("Variables de proveedor heredadas se aíslan al lanzar: "+", ".join(inherited))
    if shutil.disk_usage(manager.home).free<2*1024**3:warnings.append("Menos de 2 GiB libres en el host")
    for message in problems:print(colored("ERROR: "+message,'empty'))
    for message in warnings:print(colored("AVISO: "+message,'low'))
    print("Diagnóstico sin turnos al modelo; el resume real entre cuentas sigue sujeto a las reglas del proveedor.")
    return 1 if problems else 0


def status(manager,args):
    cwd=Path.cwd();data=read_json(manager.state/'projects.json').get(str(cwd),{})
    print("Proyecto: "+str(cwd))
    print("Última selección en este directorio: "+(f"{data['provider']} {data['account']}" if data else "ninguna"))
    print("La selección es por lanzamiento; otras terminales conservan su cuenta.")
    return accounts(manager,argparse.Namespace(json=False))


def menu(manager,switch=False):
    while True:
        data=manager.cache().get('accounts',{})
        current=read_json(manager.state/'projects.json').get(str(Path.cwd()),{})
        options=[]
        for a in manager.accounts():
            row=data.get(manager.key(a),{});value=row.get('available_percent') if row.get('status')=='OK' else None
            selected=current.get('provider')==a['provider'] and current.get('account')==a['account']
            options.append((('launch',a),colored(f"{'✓' if selected else ' '} {a['label']:<12}",a['provider'])+' '+colored(a.get('email') or 'correo pendiente','bold')+f'  {pct(value):>8}'+(' · SIN LOGIN' if not manager.has_auth(a) else '')))
        for tool in current_tools(manager):
            options.append((('tool',tool),colored(tool['label'],tool['provider'])+' · cuenta actual'+(' · modelos FREE' if tool['provider']=='opencode' else '')))
        if not switch:options += [(('resume',None),'[r] Resume'),(('limits',None),'[l] Limits'),(('switch',None),'[s] Switch'),(('handoff',None),'[h] Handoff'),(('doctor',None),'[d] Doctor')]
        chosen=choose('AI MANAGER — '+str(Path.cwd()),options)
        if chosen is None:return 0
        action,a=chosen
        if action=='tool':return launch_tool(manager,argparse.Namespace(provider=a['provider'],model=None,dry_run=False,extra=[]))
        if action in ('launch','resume'):
            if a is None:
                a=choose('Proveedor y cuenta para resume',[(a,a['label']) for a in manager.accounts()])
                if a is None:continue
            args=argparse.Namespace(provider=a['provider'],account=a['account'],pick=False,dry_run=False,extra=[])
            # Switch manually selects a target; resume if this provider has an
            # existing local session, otherwise start with the handoff if present.
            return launch(manager,args,resume=action=='resume' or (switch and bool(list_sessions(manager,a['provider'],Path.cwd()))))
        if action=='limits':limits(manager,argparse.Namespace(cached=False,refresh=False,json=False))
        if action=='handoff':handoff(manager,Path.cwd(),{})
        if action=='doctor':doctor(manager,None)
        if action=='switch':return menu(manager,True)


def parser():
    p=argparse.ArgumentParser(prog='ai',description='Cuentas legítimas independientes y sesiones en el mismo proyecto.')
    p.add_argument('--version',action='version',version='AI Command '+__version__)
    sub=p.add_subparsers(dest='command')
    a=sub.add_parser('setup',help='Preparar registro sin copiar credenciales');a.add_argument('--empty',action='store_true',help='Registro vacío para dar de alta sólo tus cuentas')
    a=sub.add_parser('update',help='Actualizar el gestor desde la última release estable')
    mode=a.add_mutually_exclusive_group();mode.add_argument('--check',action='store_true');mode.add_argument('--rollback',action='store_true');mode.add_argument('--list',action='store_true');mode.add_argument('--to',metavar='vX.Y.Z')
    a=sub.add_parser('migrate',help='Aplicar migraciones privadas de configuración');a.add_argument('--dry-run',action='store_true')
    a=sub.add_parser('providers-update',help='Mantener Codex, Claude, AGY y OpenCode con sus instaladores oficiales')
    mode=a.add_mutually_exclusive_group();mode.add_argument('--check',action='store_true');mode.add_argument('--cron',choices=['on','off','status']);mode.add_argument('--scheduled',action='store_true')
    a.add_argument('--json',action='store_true')
    a=sub.add_parser('configure',help='Preferencias del gestor')
    for provider in ('codex','claude','agy','opencode'):a.add_argument('--'+provider+'-danger',choices=['on','off'])
    a=sub.add_parser('native',help='CLI original de AGY/OpenCode con tu política de permisos')
    a.add_argument('provider',choices=list(SINGLE_TOOLS))
    a.add_argument('arguments',nargs=argparse.REMAINDER)
    sub.add_parser('vscode-install',help='Instalar extensiones de agentes/terminales para este usuario SSH')
    install=sub.add_parser('install',help='Instalar comandos en PATH');install.add_argument('--bin-dir',default=Manager().command_bin())
    install.add_argument('--auto',action='store_true',help='Instalar también codex/claude automáticos en ~/.local/bin')
    install.add_argument('--auto-bin-dir',default=str(Path.home()/'.local/bin'))
    install.add_argument('--shell',action='store_true',help='Preparar agy/opencode directos en Bash conservando los binarios')
    a=sub.add_parser('auto',help='Elegir cuenta por cuota fresca; resume sólo si se pide')
    a.add_argument('provider',choices=['codex','claude'])
    mode=a.add_mutually_exclusive_group();mode.add_argument('--new',action='store_true');mode.add_argument('--resume',action='store_true')
    a.add_argument('--session');a.add_argument('--pick',action='store_true');a.add_argument('--dry-run',action='store_true')
    for name in ('status','doctor','switch'):sub.add_parser(name)
    a=sub.add_parser('accounts');a.add_argument('--json',action='store_true');a.add_argument('--all',action='store_true')
    for name in ('limits','usage'):
        a=sub.add_parser(name);group=a.add_mutually_exclusive_group();group.add_argument('--refresh',action='store_true');group.add_argument('--cached',action='store_true');a.add_argument('--json',action='store_true');a.add_argument('--color',choices=['auto','always','never'],default='auto')
        a.add_argument('provider',nargs='?',choices=['codex','claude',*SINGLE_TOOLS],help='Consultar sólo este proveedor')
        a.add_argument('--layout',choices=['auto','columns','stacked'],default='auto')
        a.add_argument('--monitoring',action='store_true',help='Panel vivo: consulta en segundo plano cada 5 minutos; q salir, r actualizar')
    a=sub.add_parser('add',help='Alta rápida por correo');a.add_argument('provider',choices=['codex','claude']);a.add_argument('email');a.add_argument('--id',dest='number');a.add_argument('--home')
    for name in ('enable','disable'):
        a=sub.add_parser(name);a.add_argument('provider',choices=['codex','claude']);a.add_argument('account')
    a=sub.add_parser('rename');a.add_argument('provider',choices=['codex','claude']);a.add_argument('old');a.add_argument('new')
    a=sub.add_parser('policy',help='Reservar una cuenta Codex para un modelo verificado')
    a.add_argument('provider',choices=['codex']);a.add_argument('account');a.add_argument('--model');a.add_argument('--auto',choices=['on','off']);a.add_argument('--plan')
    a=sub.add_parser('priority',help='Prioridad de una cuenta en la selección automática')
    a.add_argument('provider',choices=['codex','claude']);a.add_argument('account')
    a.add_argument('priority',choices=['normal','low'])
    a=sub.add_parser('login');a.add_argument('provider',choices=['codex','claude']);a.add_argument('account');a.add_argument('--reauth',action='store_true')
    for provider in ('codex','claude'):
        a=sub.add_parser(provider);a.add_argument('account');a.add_argument('--dry-run',action='store_true')
    for provider in SINGLE_TOOLS:
        a=sub.add_parser(provider,help='Usar la cuenta actual');a.add_argument('--model');a.add_argument('--dry-run',action='store_true')
    a=sub.add_parser('models');a.add_argument('provider',choices=list(SINGLE_TOOLS));a.add_argument('--json',action='store_true');a.add_argument('--refresh',action='store_true')
    a=sub.add_parser('activity',help='Tokens locales y referencias históricas; no cuota oficial')
    a.add_argument('provider',choices=['opencode']);a.add_argument('--json',action='store_true')
    a=sub.add_parser('resume');a.add_argument('provider',choices=['codex','claude',*SINGLE_TOOLS]);a.add_argument('account',nargs='?');a.add_argument('--pick',action='store_true');a.add_argument('--dry-run',action='store_true');a.add_argument('--model')
    a.add_argument('--session',help='ID explícito, también de una importación de otro directorio')
    a=sub.add_parser('sessions');a.add_argument('provider',choices=['codex','claude',*SINGLE_TOOLS])
    a.add_argument('--all',action='store_true',help='Mostrar IDs y directorios de todos los proyectos')
    a=sub.add_parser('handoff')
    for field in ('task','goal','done','tests','failing','decision','remaining','next'):a.add_argument('--'+field)
    return p


def main(argv=None):
    os.umask(0o077)
    original_argv=list(sys.argv[1:] if argv is None else argv)
    p=parser();args,extra=p.parse_known_args(original_argv)
    if extra and args.command not in ('codex','claude','agy','opencode','resume','auto'):p.error('Argumentos desconocidos: '+str(extra))
    args.extra=extra
    manager=Manager()
    try:
        from .update_notice import maybe_update
        maybe_update(manager,original_argv,args)
        if args.command=='update':
            from .updater import update
            return update(args)
        if args.command=='providers-update':
            from .provider_updates import configure_cron,run_updates
            if args.cron:
                enabled=configure_cron(manager,args.cron)
                print('Actualizaciones de CLI: '+('ON · cada 6 horas (minuto 17)' if enabled else 'OFF'))
                return 0
            rows=run_updates(manager,args.check)
            if args.json:print(json.dumps({'providers':rows},indent=2))
            else:
                for row in rows:print(row['provider']+': '+row['status']+' · '+str(row.get('before',''))+
                                      (' → '+row['after'] if row.get('after') else '')+
                                      (' · '+row['reason'] if row.get('reason') else ''))
            return int(any(row['status'] in ('ERROR','RESTORED','RECOVERY_NEEDED') for row in rows))
        if args.command=='vscode-install':
            from .vscode import install_extensions
            install_extensions(manager)
            return 0
        if args.command=='migrate':
            steps=migrate(manager,args.dry_run)
            print('\n'.join(steps) or 'Configuración al día.')
            return 0
        if args.command=='setup':
            if args.empty and not manager.config_path.exists():
                private_dir(manager.config_dir);private_dir(manager.state)
                write_json(manager.config_path,{'schema':1,'manager_schema':CURRENT,'accounts':[],
                    'claude_skip_permissions':False})
                print('Registro vacío preparado. Añade cuentas con ai add.')
            else:setup(manager)
            migrate(manager)
            return 0
        if args.command=='install':
            source=Path(__file__).resolve().parent.parent
            if args.shell:
                from .setup import install_native_shell
                install_native_shell(manager)
            else:install_commands(source,args.bin_dir,manager.accounts(include_inactive=True) or None)
            if args.auto:install_auto_commands(source,args.auto_bin_dir)
            return 0
        if args.command=='native':
            from .entrypoints import single_provider_main
            arguments=args.arguments[1:] if args.arguments[:1]==['--'] else args.arguments
            return single_provider_main(args.provider,arguments)
        migrate(manager)
        manager.require_setup()
        if args.command=='configure':
            policies={provider:getattr(args,provider+'_danger') for provider in ('codex','claude','agy','opencode')
                      if getattr(args,provider+'_danger') is not None}
            if not policies:raise ManagerError('Elige --codex-danger, --claude-danger, --agy-danger o --opencode-danger on/off')
            with manager.lock('registry',timeout=15):
                backup_files(manager.home,[manager.config_path],'Change explicit provider permission policies')
                manager.config=read_json(manager.config_path)
                for provider,value in policies.items():manager.config[provider+'_skip_permissions']=value=='on'
                write_json(manager.config_path,manager.config)
            for provider,value in policies.items():
                print(provider+' auto-approve: '+value+' · aplica a nuevos lanzamientos y resume')
            return 0
        if args.command=='activity':
            activity=local_activity(manager)
            if args.json:print(json.dumps(activity,indent=2));return 0
            print(colored('AI ACTIVITY · OpenCode','title'))
            for line in activity_lines(activity):print(line)
            for model in activity['models']:
                if not model['errors_429']:continue
                print('\n'+colored(model['provider']+'/'+model['model'],'bold')+
                      f" · {model['errors_429']} errores 429 · {model['independent_429_bursts']} grupos")
                for window in model['windows']:
                    reference=window['median_tokens_before_429']
                    print('  '+window['name']+': '+compact_tokens(window['tokens']['total'])+' tokens actuales · mediana antes del 429: '+
                          (compact_tokens(reference)+' tokens' if reference is not None else 'UNKNOWN'))
            print('\nLos 429 no demuestran un límite de tokens ni una ventana de reset; confianza baja. No se infiere disponibilidad.')
            return 0
        if args.command=='priority':
            account=manager.account(args.provider,args.account)
            set_priority(manager,account,args.priority)
            print(colored(account['label'],account['provider'])+' · '+colored(
                '↓ Prioridad baja · última opción automática' if args.priority=='low' else 'Prioridad normal',
                'low' if args.priority=='low' else 'ok'))
            return 0
        if args.command=='policy':
            account=manager.account(args.provider,args.account)
            if args.model is None and args.auto is None and args.plan is None:raise ManagerError('Indica --model, --auto on/off o --plan')
            set_codex_policy(manager,account,args.model,None if args.auto is None else args.auto=='on',args.plan)
            print(account['label']+' · '+account['email']+' · '+(account.get('plan_label') or '')+' · '+(account.get('fixed_model') or 'modelo del CLI')+
                  (' · selección manual' if account.get('automatic') is False else ' · selección automática'))
            return 0
        if args.command in SINGLE_TOOLS:args.provider=args.command;return launch_tool(manager,args)
        if args.command=='models':
            catalog=models(manager,args.provider,args.refresh)
            if args.json:print(json.dumps(catalog,indent=2))
            else:
                print(colored('AI MODELS · '+args.provider,'title'))
                for item in catalog:print(colored(item['id'],args.provider)+' · '+('FREE · tarifa de catálogo 0' if args.provider=='opencode' else item['name']))
            return 0
        if args.command=='auto':return auto_launch(manager,args)
        if args.command in ('codex','claude'):args.provider=args.command;return launch(manager,args)
        if args.command=='resume':
            if args.provider in SINGLE_TOOLS:
                if args.account:raise ManagerError('Esta herramienta usa sólo la cuenta actual: ai resume '+args.provider)
                return launch_tool(manager,args,True)
            if not args.account:raise ManagerError('Indica la cuenta: ai resume '+args.provider+' <cuenta>')
            return launch(manager,args,True)
        if args.command=='accounts':return accounts(manager,args)
        if args.command in ('limits','usage'):
            os.environ['AI_MANAGER_COLOR']=args.color;return limits(manager,args)
        if args.command in ('add','rename','enable','disable'):
            with manager.lock('registry',blocking=False):
                if args.command=='add':a=add_account(manager,args.provider,args.email,args.number,args.home)
                elif args.command=='rename':a=rename_account(manager,args.provider,args.old,args.new)
                else:
                    a=manager.account(args.provider,args.account,allow_inactive=True);a['enabled']=args.command=='enable';save_registry(manager,'Change account activation without deleting data')
                install_commands(Path(__file__).resolve().parent.parent,
                                 manager.command_bin(),
                                 manager.accounts(include_inactive=True))
            print(f"{a['label']} · {a.get('email') or 'correo pendiente'} · {'activa' if a.get('enabled',True) else 'desactivada'}")
            if not manager.has_auth(a) and a.get('enabled',True):print(f"Login una vez: ai login {a['provider']} {a['account']}")
            return 0
        if args.command=='login':return login(manager,args)
        if args.command=='doctor':return doctor(manager,args)
        if args.command=='status':return status(manager,args)
        if args.command=='sessions':
            if args.provider in SINGLE_TOOLS:
                for row in list_tool_sessions(manager,args.provider,Path.cwd()):print(row['id']+' · cuenta actual · '+str(Path.cwd()))
                return 0
            for row in list_sessions(manager,args.provider,None if args.all else Path.cwd()):
                print(f"{row['id']} · cuenta {row['origin_account']} · {dt.datetime.fromtimestamp(row['updated']).astimezone().isoformat(timespec='minutes')}"+
                      (' · '+row['cwd'] if args.all else ''))
            return 0
        if args.command=='handoff':handoff(manager,Path.cwd(),vars(args));return 0
        return menu(manager,args.command=='switch')
    except ManagerError as exc:
        print('ai: '+str(exc),file=sys.stderr);return 1
    except KeyboardInterrupt:return 130
    except BrokenPipeError:return 0
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print('ai: operación fallida ('+type(exc).__name__+'); no se muestran datos privados.',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())

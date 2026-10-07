"""Fast account registration and reversible activation, identified by email."""
import json
from pathlib import Path
import re
import subprocess
import tomllib

from .core import ManagerError, atomic_write, backup_files, private_dir, read_json, write_json
from .providers import CodexRPC, executable
from .setup import clean_settings, install_commands, toml_dump


def identity(manager,account):
    if not manager.has_auth(account):return None
    if account['provider']=='codex':
        with CodexRPC(manager,account) as rpc:
            data=rpc.call('account/read',{'refreshToken':False}).get('account') or {}
    else:
        result=subprocess.run([executable('claude'),'auth','status','--json'],capture_output=True,
                              text=True,timeout=12,env=manager.env(account),cwd=manager.state)
        try:data=json.loads(result.stdout)
        except ValueError:return None
        if result.returncode or data.get('loggedIn') is not True:return None
    email=data.get('email')
    return email if isinstance(email,str) and re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+',email) else None


def save_registry(manager,purpose):
    backup_files(manager.home,[manager.config_path],purpose)
    manager.config['accounts'].sort(key=lambda a:(0 if a['provider']=='codex' else 1,int(a['account'])))
    write_json(manager.config_path,manager.config)


def sync_identity(manager,account):
    observed=identity(manager,account)
    if not observed:return None
    expected=account.get('email')
    if expected and expected.lower()!=observed.lower():
        raise ManagerError(f"Identidad distinta: esperada {expected}; el CLI usa {observed}. "
                           "No se cambia la asignación. Revisa el login de esa cuenta.")
    if not account.get('email_verified'):
        with manager.lock('registry',blocking=False):
            manager.config=read_json(manager.config_path)
            current=manager.account(account['provider'],account['account'])
            current['email']=observed;current['email_verified']=True
            save_registry(manager,'Record email reported by official CLI')
            account.update(email=observed,email_verified=True)
    return observed


def add_account(manager,provider,email,number=None,home=None):
    if not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',email):
        raise ManagerError('Correo no válido')
    records=manager.accounts(provider,include_inactive=True)
    duplicate=next((a for a in records if a.get('email','').lower()==email.lower()),None)
    if duplicate:
        if number is None or duplicate['account']==str(number):
            duplicate['enabled']=True;save_registry(manager,'Reactivate registered account');return duplicate
        raise ManagerError('Este correo ya está registrado con otro número')
    if number is None:
        number=1
        while any(a['account']==str(number) for a in records):number+=1
    if not str(number).isdigit() or int(number)<1:raise ManagerError('El identificador debe ser un entero positivo')
    existing=next((a for a in records if a['account']==str(number)),None)
    if existing:
        if existing.get('email') and existing['email'].lower()!=email.lower():
            raise ManagerError('Ese número ya corresponde a otro correo; no se sobrescribe')
        if manager.has_auth(existing):
            actual=identity(manager,existing)
            if actual and actual.lower()!=email.lower():raise ManagerError('El login existente pertenece a '+actual)
        existing.update(email=email,email_verified=False,enabled=True)
        save_registry(manager,'Assign email to existing account descriptor');return existing
    profile=manager.home/f'.{provider}-account-{number}'
    if profile.exists():raise ManagerError('Existe un perfil no registrado: '+str(profile))
    target=Path(home).expanduser().absolute() if home else profile
    account={'provider':provider,'account':str(number),'label':f'{provider.title()} {number}',
             'profile':str(profile),'home':str(target),'legacy':target==manager.home/('.'+provider),
             'email':email,'email_verified':False,'enabled':True}
    if home:
        if not target.is_dir() or target.is_symlink():raise ManagerError('El home debe ser un directorio real existente')
        actual=identity(manager,account)
        if not actual or actual.lower()!=email.lower():raise ManagerError('El home no tiene un login verificable de ese correo')
        if any(Path(a['home']).resolve()==target.resolve() for a in manager.accounts(include_inactive=True)):
            raise ManagerError('Ese home ya está registrado; usa ai rename')
        account['email_verified']=True
    private_dir(profile)
    if provider=='codex':
        first=manager.accounts('codex',include_inactive=True)
        shared=next((a['shared_sqlite_home'] for a in first if a.get('shared_sqlite_home')),str(manager.home/'.codex'))
        account['shared_sqlite_home']=shared
    if not home:
        original=manager.home/('.'+provider)
        if provider=='codex':
            source=original/'config.toml';data=clean_settings(tomllib.loads(source.read_text()) if source.exists() else {})
            for key in ('sqlite_home','log_dir','model_catalog_json'):data.pop(key,None)
            data.update(cli_auth_credentials_store='file',sqlite_home=account['shared_sqlite_home'])
            atomic_write(target/'config.toml',toml_dump(data))
        else:
            write_json(target/'settings.json',clean_settings(read_json(original/'settings.json')))
    write_json(profile/'profile.json',{'effective_home':str(target),'legacy':account['legacy'],'note':'Credentials never copied'})
    manager.config['accounts'].append(account)
    save_registry(manager,'Register independent account by email')
    return account


def rename_account(manager,provider,old,new):
    account=manager.account(provider,old,allow_inactive=True)
    if not str(new).isdigit() or int(new)<1:raise ManagerError('Identificador no válido')
    if any(a['account']==str(new) for a in manager.accounts(provider,include_inactive=True)):
        raise ManagerError('Identificador ya registrado')
    profile=manager.home/f'.{provider}-account-{new}'
    if profile.exists():raise ManagerError('El nuevo descriptor ya existe; no se sobrescribe')
    private_dir(profile)
    write_json(profile/'profile.json',{'effective_home':account['home'],'legacy':account.get('legacy',False),'note':'Renumbered without moving credentials'})
    previous_number=account['account']
    account.update(account=str(new),label=f'{provider.title()} {new}',profile=str(profile))
    audit_path=manager.state/'sharing-audit.json'
    audit=read_json(audit_path)
    if audit:
        backup_files(manager.home,[audit_path],'Preserve instruction link audit before renumbering')
        for item in audit.get('files',[]):
            if item.get('provider')==provider and item.get('account')==previous_number:item['account']=str(new)
        write_json(audit_path,audit)
    save_registry(manager,'Renumber account; keep original authentication home')
    return account


def set_codex_policy(manager,account,model=None,automatic=None,plan=None):
    """Explicit routing preference; model access is still enforced by Codex."""
    if account['provider']!='codex':raise ManagerError('Esta política de modelo sólo corresponde a Codex')
    if model:
        if not sync_identity(manager,account):raise ManagerError('Se necesita un login verificable antes de fijar el modelo')
        with CodexRPC(manager,account) as rpc:
            catalog=rpc.call('model/list',{'includeHidden':False}).get('data',[])
        if model not in {item.get('model') for item in catalog if isinstance(item,dict)}:
            raise ManagerError('Ese modelo no aparece en el catálogo oficial de esta cuenta')
    if plan and not re.fullmatch(r'[A-Za-z0-9 +.-]{1,32}',plan):raise ManagerError('Nombre de plan no válido')
    with manager.lock('registry',blocking=False):
        manager.config=read_json(manager.config_path)
        current=manager.account('codex',account['account'])
        if model:current['fixed_model']=model
        if automatic is not None:current['automatic']=automatic
        if plan:current['plan_label']=plan
        save_registry(manager,'Set explicit account model and automatic routing policy')
        account.update(current)


def fixed_model_arguments(account,extras):
    model=account.get('fixed_model')
    if not model:return []
    for i,arg in enumerate(extras):
        if (arg in ('--model','-m','--profile','-p') or arg.startswith(('--model=','--profile='))
                or arg.startswith('-m') or arg.startswith('-p')):
            raise ManagerError(account['label']+' está reservada para '+model+'; no admite otro modelo/perfil al abrir')
        config=extras[i+1] if arg in ('-c','--config') and i+1<len(extras) else arg.split('=',1)[1] if arg.startswith('--config=') else arg[2:] if arg.startswith('-c') else ''
        key=config.split('=',1)[0].strip()
        if key=='model' or key.startswith('profiles.'):
            raise ManagerError('La cuenta tiene un modelo reservado; no se sobrescribe con --config')
    return ['--model',model]

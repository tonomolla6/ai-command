"""Install bundled local extensions for the current Remote SSH user."""
import json
from pathlib import Path
import shutil
import time

from .core import ManagerError, atomic_write, backup_files, read_json


def install_extensions(manager):
    source=Path(__file__).resolve().parent.parent/'integrations/vscode'
    target=manager.home/'.vscode-server/extensions'
    target.mkdir(parents=True,exist_ok=True)
    registry=target/'extensions.json'
    obsolete=target/'.obsolete'
    machine=manager.home/'.vscode-server/data/Machine/settings.json'
    try:
        entries=json.loads(registry.read_text()) if registry.exists() else []
        settings=json.loads(machine.read_text()) if machine.exists() else {}
        retired=json.loads(obsolete.read_text()) if obsolete.exists() else {}
    except ValueError:
        raise ManagerError('VS Code usa JSONC/JSON no estándar; se conserva, instala las extensiones manualmente') from None
    if not isinstance(entries,list) or not isinstance(settings,dict) or not isinstance(retired,dict):
        raise ManagerError('Registro VS Code desconocido; se conserva')
    backup_files(manager.home,[registry,machine,obsolete],'Install requested AI Command VS Code extensions')
    installed=[]
    for name in ('agentes-terminales','terminales-persistentes'):
        folder=source/name
        package=read_json(folder/'package.json')
        ident=package['publisher']+'.'+package['name']
        relative=ident+'-'+package['version']
        timestamp=next((e.get('metadata',{}).get('installedTimestamp') for e in entries
                        if e.get('identifier',{}).get('id')==ident and e.get('version')==package['version']),None)
        dest=target/relative
        helpers={}
        if name=='terminales-persistentes':
            helpers={Path('bin')/filename:source/'bin'/filename
                     for filename in ('terminales','terminales-native')}
            if any(not path.is_file() for path in helpers.values()):
                raise ManagerError('Paquete de terminales incompleto; faltan los lanzadores incluidos')
        expected={p.relative_to(folder):p for p in folder.rglob('*') if p.is_file()}
        expected.update(helpers)
        if not dest.exists():
            shutil.copytree(folder,dest)
            for relative_helper,path in helpers.items():
                output=dest/relative_helper
                output.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,output)
                output.chmod(0o755)
        elif any(not (dest/relative_path).is_file() or (dest/relative_path).read_bytes()!=p.read_bytes()
                 for relative_path,p in expected.items()):
            raise ManagerError('Extensión de la misma versión con cambios locales conservada: '+relative)
        remaining=[]
        for entry in entries:
            previous=entry.get('identifier',{}).get('id','')
            if previous.rsplit('.',1)[-1]!=package['name']:
                remaining.append(entry);continue
            old=entry.get('relativeLocation')
            if (isinstance(old,str) and Path(old).name==old and old!=relative and
                    old==previous+'-'+entry.get('version','')):
                retired[old]=True
        entries=remaining
        retired.pop(relative,None)
        entries.append({'identifier':{'id':ident},'version':package['version'],
            'location':{'$mid':1,'path':str(dest),'scheme':'file'},'relativeLocation':relative,
            'metadata':{'isApplicationScoped':False,'isMachineScoped':True,'isBuiltin':False,
                        'installedTimestamp':timestamp or int(time.time()*1000),'pinned':True,'source':'vsix'}})
        installed.append(package['displayName']+' '+package['version'])
    machine.parent.mkdir(parents=True,exist_ok=True)
    settings['terminal.integrated.defaultProfile.linux']='AI Command tmux persistente'
    # VS Code owns graphical tab/split layout; tmux owns the programs. The
    # pinned profile retains its persistent key rather than relaunching agents.
    settings['terminal.integrated.enablePersistentSessions']=True
    atomic_write(registry,json.dumps(entries,indent=2)+'\n',0o644)
    atomic_write(obsolete,json.dumps(retired,indent=2)+'\n',0o644)
    atomic_write(machine,json.dumps(settings,indent=2)+'\n',0o600)
    print(' y '.join(installed)+' instalados para este usuario.')
    print('Recarga la ventana de VS Code Remote SSH para activarlos; tmux debe estar instalado.')

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
    machine=manager.home/'.vscode-server/data/Machine/settings.json'
    try:
        entries=json.loads(registry.read_text()) if registry.exists() else []
        settings=json.loads(machine.read_text()) if machine.exists() else {}
    except ValueError:
        raise ManagerError('VS Code usa JSONC/JSON no estándar; se conserva, instala las extensiones manualmente') from None
    if not isinstance(entries,list) or not isinstance(settings,dict):
        raise ManagerError('Registro VS Code desconocido; se conserva')
    backup_files(manager.home,[registry,machine],'Install requested AI Command VS Code extensions')
    for name in ('agentes-terminales','terminales-persistentes'):
        folder=source/name
        package=read_json(folder/'package.json')
        ident=package['publisher']+'.'+package['name']
        relative=ident+'-'+package['version']
        dest=target/relative
        if not dest.exists():
            shutil.copytree(folder,dest)
        elif any(not (dest/p.relative_to(folder)).is_file() or (dest/p.relative_to(folder)).read_bytes()!=p.read_bytes()
                 for p in folder.rglob('*') if p.is_file()):
            raise ManagerError('Extensión de la misma versión con cambios locales conservada: '+relative)
        entries=[e for e in entries if e.get('identifier',{}).get('id')!=ident]
        entries.append({'identifier':{'id':ident},'version':package['version'],
            'location':{'$mid':1,'path':str(dest),'scheme':'file'},'relativeLocation':relative,
            'metadata':{'isApplicationScoped':False,'isMachineScoped':True,'isBuiltin':False,
                        'installedTimestamp':int(time.time()*1000),'pinned':True,'source':'vsix'}})
    machine.parent.mkdir(parents=True,exist_ok=True)
    settings['terminal.integrated.defaultProfile.linux']='AI Command tmux persistente'
    atomic_write(registry,json.dumps(entries,indent=2)+'\n',0o644)
    atomic_write(machine,json.dumps(settings,indent=2)+'\n',0o600)
    print('AI Command Agentes 0.1.8 y AI Command Terminales 0.4.2 instalados para este usuario.')
    print('Recarga la ventana de VS Code Remote SSH para activarlos; tmux debe estar instalado.')

"""Quiet, locked maintenance using official installers and code-only backups."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile

from .core import ManagerError, atomic_write, now, private_dir, read_json, write_json
from .providers import executable
from .distribution import file_hash

PROVIDERS=('codex','claude','agy','opencode')
PACKAGES={'codex':'@openai/codex','claude':'@anthropic-ai/claude-code'}
CRON=Path('/etc/cron.d/ai-command-providers')
LOG=Path('/var/log/ai-command/providers-update.log')
ROTATE=Path('/etc/logrotate.d/ai-command-providers')
MARKER='# ai-command provider maintenance v1'
FLOOR=15*1024**3


class InsufficientSpace(ManagerError):
    pass


def update_plan(provider):
    binary=Path(executable(provider)).resolve()
    if provider in PACKAGES:
        package=next((p for p in binary.parents if read_json(p/'package.json').get('name')==PACKAGES[provider]),None)
        if package:
            modules=next((p for p in package.parents if p.name=='node_modules' and p.parent.name=='lib'),None)
            npm=shutil.which('npm')
            if not modules or not npm:raise ManagerError('Instalación npm no administrable')
            return {'provider':provider,'path':package,'binary':binary,
                    'command':[npm,'install','--global','--prefix',str(modules.parent.parent),
                               PACKAGES[provider]+'@latest','--no-audit','--no-fund']}
    with binary.open('rb') as stream:native=stream.read(4)==b'\x7fELF'
    if provider in ('codex','claude','opencode') and native:
        return {'provider':provider,'path':binary,'binary':binary,'native':True}
    if provider in PACKAGES:raise ManagerError('Instalación nativa no verificable')
    command=[str(binary),'update'] if provider in ('claude','agy') else [str(binary),'--pure','upgrade']
    return {'provider':provider,'path':binary,'binary':binary,'command':command}


def busy(plan, proc=Path('/proc')):
    root=str(plan['path']);directory=plan['path'].is_dir()
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
        try:
            target=os.readlink(entry/'exe').removesuffix(' (deleted)')
            args=(entry/'cmdline').read_bytes().split(b'\0')
            paths=[target,*[os.fsdecode(a) for a in args[:3]]]
            if any(p==root or (directory and p.startswith(root+'/')) for p in paths):return True
        except (OSError,PermissionError):continue
    return False


def environment(provider):
    env=dict(os.environ);env['CI']='1'
    if provider=='agy':env['AGY_CLI_DISABLE_AUTO_UPDATE']='1'
    return env


def current_version(plan):
    result=subprocess.run([str(plan['binary']),'--version'],capture_output=True,text=True,
                          env=environment(plan['provider']),timeout=15)
    match=re.search(r'\b\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?\b',result.stdout)
    return match.group() if result.returncode==0 and match else None


def code_files(path):
    return sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]


def snapshot(manager,plan,version):
    source=plan['path'];digest=hashlib.sha256();size=0
    if source.is_dir() and any(p.is_symlink() and not p.resolve().is_relative_to(source) for p in source.rglob('*')):
        raise ManagerError('El código enlaza rutas ajenas; backup rechazado')
    for path in code_files(source):
        if source.is_dir() and not path.resolve().is_relative_to(source):
            raise ManagerError('El código enlaza archivos ajenos; backup rechazado')
        digest.update(str(path.relative_to(source) if source.is_dir() else path.name).encode())
        digest.update(str(path.stat().st_mode & 0o777).encode())
        size+=path.stat().st_size
        with path.open('rb') as stream:
            while chunk:=stream.read(1024*1024):digest.update(chunk)
    if shutil.disk_usage(source.parent).free < FLOOR+3*size+64*1024**2:
        raise InsufficientSpace('Espacio reservado insuficiente')
    backups=private_dir(manager.home/'.ai-manager/backups/provider-updates')
    identity=plan['provider']+'-'+version+'-'+digest.hexdigest()[:16]
    destination=backups/identity
    if not destination.exists():
        private_dir(destination)
        archive=destination/'code.tar.gz'
        try:
            with tarfile.open(archive,'w:gz') as tar:tar.add(source,arcname='payload')
            archive.chmod(0o600)
            write_json(destination/'manifest.json',{'created_at':now(),'source':str(source),
                       'version':version,'code_sha256':digest.hexdigest(),
                       'archive_sha256':file_hash(archive)})
        except BaseException:
            # Only this incomplete snapshot is removed; prior backups are kept.
            shutil.rmtree(destination)
            raise
    meta=read_json(destination/'manifest.json');archive=destination/'code.tar.gz'
    if meta.get('source')!=str(source) or meta.get('archive_sha256')!=file_hash(archive):
        raise ManagerError('Backup de código no verificable')
    return archive


def restore_code(plan,archive):
    target=plan['path'];original=target.stat() if target.exists() else None
    stage=Path(tempfile.mkdtemp(prefix='.ai-command-restore-',dir=target.parent))
    displaced=stage/'failed-code'
    try:
        with tarfile.open(archive,'r:gz') as tar:
            if any(Path(m.name).is_absolute() or '..' in Path(m.name).parts or Path(m.name).parts[0]!='payload' for m in tar):
                raise ManagerError('Backup de código inseguro')
            tar.extractall(stage,filter='data')
        if target.exists():os.rename(target,displaced)
        try:os.rename(stage/'payload',target)
        except BaseException:
            if displaced.exists():os.rename(displaced,target)
            raise
        if original and os.getuid()==0:os.chown(target,original.st_uid,original.st_gid)
    finally:
        if (stage/'payload').exists() and not target.exists():
            # Preserve the exact recovery directory if restoring could not finish.
            raise ManagerError('Restauración incompleta; código de recuperación conservado')
        shutil.rmtree(stage)


def run_updates(manager,check=False):
    rows=[]
    with manager.lock('provider-updates',timeout=0.1):
        for provider in PROVIDERS:
            row={'provider':provider,'status':'UNKNOWN'};archive=None;plan=None
            try:
                plan=update_plan(provider)
                if busy(plan):row['status']='BUSY';rows.append(row);continue
                before=current_version(plan);row['before']=before
                if not before:raise ManagerError('Versión nativa no verificable')
                if check:row['status']='READY';rows.append(row);continue
                archive=snapshot(manager,plan,before)
                original=plan['path'].stat()
                if plan.get('native'):
                    from .native_updates import install_native
                    install_native(plan,before,environment(provider))
                    returncode=0
                else:
                    result=subprocess.run(plan['command'],stdin=subprocess.DEVNULL,capture_output=True,
                                          env=environment(provider),timeout=600)
                    returncode=result.returncode
                # Retain native executables' access mode on shared installations.
                if plan['path'].is_file():
                    plan['path'].chmod(original.st_mode & 0o777)
                    if os.getuid()==0:os.chown(plan['path'],original.st_uid,original.st_gid)
                after=current_version(plan)
                if returncode or not after:raise ManagerError('Falló el actualizador oficial')
                row.update(status='UPDATED' if after!=before else 'CURRENT',after=after,backup=str(archive))
            except InsufficientSpace:
                row.update(status='SKIPPED_SPACE',reason='Reserva de 15 GiB y espacio de actualización insuficiente')
            except (ManagerError,OSError,ValueError,KeyError,subprocess.SubprocessError,tarfile.TarError):
                row['status']='ERROR'
                if archive and plan:
                    try:
                        restore_code(plan,archive)
                        row['status']='RESTORED' if current_version(plan)==row.get('before') else 'RECOVERY_NEEDED'
                    except (ManagerError,OSError,subprocess.SubprocessError,tarfile.TarError):row['status']='RECOVERY_NEEDED'
            rows.append(row)
        if not check:write_json(manager.state/'provider-updates.json',{'last_run':now(),'providers':rows})
    return rows


def configure_cron(manager,mode):
    if mode=='status':return CRON.exists() and MARKER in CRON.read_text()
    if os.getuid()!=0:raise ManagerError('La programación global requiere sudo ai providers-update --cron '+mode)
    launcher=Path(manager.command_bin())/'ai'
    for path in (CRON,ROTATE):
        if path.exists() and (path.is_symlink() or MARKER not in path.read_text()):
            raise ManagerError('Se conserva configuración de mantenimiento ajena')
    changed=[p for p in (CRON,ROTATE) if p.exists()]
    if changed:
        backup=private_dir(manager.home/'.ai-manager/backups'/dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f-cron'))
        for p in changed:
            shutil.copy2(p,backup/p.name);(backup/p.name).chmod(0o600)
    if mode=='off':
        for path in changed:path.unlink()
        return False
    LOG.parent.mkdir(mode=0o700,parents=True,exist_ok=True);LOG.parent.chmod(0o700)
    if LOG.is_symlink():raise ManagerError('Log enlazado; no se modifica')
    LOG.touch(mode=0o600,exist_ok=True);LOG.chmod(0o600)
    cron=(MARKER+'\nSHELL=/bin/sh\nPATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n'
          'HOME='+str(manager.home)+'\nMAILTO=""\n'
          '17 */6 * * * root /usr/bin/nice -n 10 '+shlex.quote(str(launcher))+
          ' providers-update --scheduled >> '+str(LOG)+' 2>&1\n')
    atomic_write(CRON,cron,0o644)
    ROTATE.parent.mkdir(parents=True,exist_ok=True)
    atomic_write(ROTATE,MARKER+'\n'+str(LOG)+' {\n weekly\n rotate 4\n compress\n missingok\n notifempty\n create 0600 root root\n}\n',0o644)
    return True

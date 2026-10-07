"""Offer a verified stable update, then replay the user's exact command."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .core import ManagerError, private_dir, read_json, write_json
from .distribution import installation, version_tuple
from .updater import release_info, update
from .ui import colored

CACHE_SECONDS = 15 * 60
REPLAY = 'AI_COMMAND_UPDATE_REPLAY'


def maybe_update(manager, argv, args):
    if os.environ.pop(REPLAY, None) == '1':
        return
    if (not sys.stdin.isatty() or not sys.stdout.isatty() or args.command == 'update'
            or any(arg in ('--json', '--dry-run', '--cached') for arg in argv)):
        return
    try:
        base=installation()
        current=read_json(base/'install.json')
        launcher=Path(current['prefix'])/'bin/ai'
        cache_path=manager.state/'update-check.json'
        private_dir(manager.state)
        with manager.lock('update-check', timeout=0.1):
            cache=read_json(cache_path)
            stamp=time.time()
            if stamp-float(cache.get('checked_at',0)) >= CACHE_SECONDS:
                # Cache a short failure as well: offline terminals keep working.
                cache={'checked_at':stamp}
                write_json(cache_path,cache)
                info=release_info(timeout=2)
                tag=info['tag_name']
                names={asset['name'] for asset in info.get('assets',[])}
                if {'ai-command-'+tag+'.tar.gz','SHA256SUMS'} <= names:
                    cache['latest']=tag
                write_json(cache_path,cache)
        tag=cache.get('latest')
        if (not tag or version_tuple(tag)<=version_tuple(current['version'])
                or stamp<float(cache.get('dismissed_until',0))):
            return
    except (ManagerError,OSError,ValueError,KeyError,TypeError):
        return
    print(colored('AI UPDATE · nueva versión '+tag+' (actual '+current['version']+')','title'),flush=True)
    try:answer=input('¿Actualizar y continuar con tu comando? [s/N] ').strip().lower()
    except EOFError:answer='n'
    if answer not in ('s','si','sí','y','yes'):
        cache['dismissed_until']=time.time()+CACHE_SECONDS
        try:write_json(cache_path,cache)
        except OSError:pass
        return
    try:
        if base.stat().st_uid == os.getuid():
            update(argparse.Namespace(rollback=False,list=False,to=tag,check=False))
        else:
            sudo=shutil.which('sudo')
            if not sudo:raise ManagerError('La instalación global requiere sudo')
            subprocess.run([sudo,'--',str(launcher),'update','--to',tag],check=True)
        # Preserve the original cwd, environment and argument boundaries. The
        # replacement process loads the NEW release through the stable launcher.
        env=dict(os.environ);env[REPLAY]='1'
        os.execve(str(launcher),[str(launcher),*argv],env)
    except (ManagerError,OSError,subprocess.SubprocessError):
        print('AI UPDATE · no se pudo actualizar; continúo con la versión instalada.',file=sys.stderr)

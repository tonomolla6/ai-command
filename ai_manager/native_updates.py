"""Verified official Linux binaries for installations without Node/npm."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import tempfile

from .core import ManagerError
from .updater import download

MAX_DOWNLOAD=256*1024**2


def stable_version(value):
    match=re.fullmatch(r'(?:rust-)?v?(\d+)\.(\d+)\.(\d+)',value)
    if not match:raise ManagerError('Versión estable del proveedor no verificable')
    return tuple(map(int,match.groups()))


def metadata(url):
    accept='application/vnd.github+json' if url.startswith('https://api.github.com/') else 'application/json'
    return json.loads(download(url,2*1024**2,accept=accept))


def release_asset(provider):
    machine=platform.machine().lower()
    arch={'x86_64':'x64','amd64':'x64','aarch64':'arm64','arm64':'arm64'}.get(machine)
    if platform.system()!='Linux' or not arch:raise ManagerError('Plataforma nativa no soportada')
    if provider=='claude':
        main=metadata('https://registry.npmjs.org/@anthropic-ai%2fclaude-code/latest')
        version=main['version'];stable_version(version)
        suffix='-musl' if platform.libc_ver()[0]=='musl' else ''
        package='@anthropic-ai/claude-code-linux-'+arch+suffix
        if main.get('name')!='@anthropic-ai/claude-code' or main.get('optionalDependencies',{}).get(package)!=version:
            raise ManagerError('Distribución oficial de Claude no verificable')
        meta=metadata('https://registry.npmjs.org/'+package.replace('/','%2f')+'/'+version)
        dist=meta['dist'];url=dist['tarball'];integrity=dist['integrity']
        if meta.get('name')!=package or meta.get('version')!=version or not url.startswith('https://registry.npmjs.org/'+package+'/-/'):
            raise ManagerError('Origen de Claude inesperado')
        if not re.fullmatch(r'sha512-[A-Za-z0-9+/]+={0,2}',integrity):raise ManagerError('Integridad de Claude ausente')
        expected=base64.b64decode(integrity[7:],validate=True).hex()
        if len(expected)!=128:raise ManagerError('Integridad de Claude no válida')
        return {'version':version,'url':url,'algorithm':'sha512','digest':expected,'binary':'claude'}
    repo={'codex':'openai/codex','opencode':'anomalyco/opencode'}[provider]
    meta=metadata('https://api.github.com/repos/'+repo+'/releases/latest')
    tag=meta['tag_name'];version='.'.join(map(str,stable_version(tag)))
    if meta.get('draft') or meta.get('prerelease'):raise ManagerError('Release nativa no estable')
    if provider=='codex':
        cpu={'x64':'x86_64','arm64':'aarch64'}[arch]
        name='codex-'+cpu+'-unknown-linux-musl.tar.gz'
    else:
        name='opencode-linux-'+arch+('-musl' if platform.libc_ver()[0]=='musl' else '')+'.tar.gz'
    assets=[a for a in meta.get('assets',[]) if a.get('name')==name]
    url='https://github.com/'+repo+'/releases/download/'+tag+'/'+name
    if len(assets)!=1 or assets[0].get('browser_download_url')!=url or not re.fullmatch(r'sha256:[0-9a-f]{64}',assets[0].get('digest','')):
        raise ManagerError('Archivo o checksum oficial no verificable')
    return {'version':version,'url':url,'algorithm':'sha256','digest':assets[0]['digest'][7:],
            'binary':name[:-7] if provider=='codex' else 'opencode'}


def extract_binary(blob,asset,target):
    if hashlib.new(asset['algorithm'],blob).hexdigest()!=asset['digest']:
        raise ManagerError('Checksum nativo incorrecto')
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as tar:
        members=tar.getmembers()
        if len(members)>1000 or sum(m.size for m in members)>1024**3:raise ManagerError('Paquete nativo excesivo')
        if any(Path(m.name).is_absolute() or '..' in Path(m.name).parts or not (m.isfile() or m.isdir()) for m in members):
            raise ManagerError('Contenido nativo inseguro')
        matches=[m for m in members if m.isfile() and Path(m.name).name==asset['binary']]
        if len(matches)!=1:raise ManagerError('Binario nativo ambiguo')
        with tar.extractfile(matches[0]) as source,target.open('xb') as output:
            while chunk:=source.read(1024**2):output.write(chunk)


def install_native(plan,before,env):
    asset=release_asset(plan['provider'])
    if stable_version(asset['version'])<=stable_version(before):return
    target=plan['binary'];original=target.stat()
    with tempfile.TemporaryDirectory(prefix='.ai-command-native-',dir=target.parent) as directory:
        candidate=Path(directory)/plan['provider']
        extract_binary(download(asset['url'],MAX_DOWNLOAD,timeout=120),asset,candidate)
        candidate.chmod(original.st_mode & 0o777)
        if os.getuid()==0:os.chown(candidate,original.st_uid,original.st_gid)
        result=subprocess.run([str(candidate),'--version'],capture_output=True,text=True,env=env,timeout=20)
        match=re.search(r'\b\d+\.\d+\.\d+\b',result.stdout)
        if result.returncode or not match or match.group()!=asset['version']:
            raise ManagerError('Versión del binario descargado no coincide')
        os.replace(candidate,target)

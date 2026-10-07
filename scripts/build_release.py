#!/usr/bin/env python3
"""Build an allowlisted public artifact, never a HOME/config export."""
import argparse
import hashlib
import io
from pathlib import Path
import sys
import tarfile

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from ai_manager import __version__
from ai_manager.distribution import payload_files, version_tuple

p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);args=p.parse_args()
version=(ROOT/'VERSION').read_text().strip();version_tuple('v'+version)
if version!=__version__:raise SystemExit('VERSION y __version__ no coinciden')
args.out.mkdir(parents=True,exist_ok=True)
paths=payload_files(ROOT)+[ROOT/'install.py',ROOT/'install.sh']
target=args.out/('ai-command-v'+version+'.tar.gz')
with tarfile.open(target,'w:gz',compresslevel=9) as tar:
 for path in sorted(paths):
  if path.is_symlink():raise SystemExit('No se publican enlaces')
  info=tar.gettarinfo(path,arcname=str(path.relative_to(ROOT)))
  info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
  info.mode=0o755 if path.name=='install.sh' or path.parent.name=='bin' else 0o644
  with path.open('rb') as data:tar.addfile(info,data)
digest=hashlib.sha256(target.read_bytes()).hexdigest()
(args.out/'SHA256SUMS').write_text(digest+'  '+target.name+'\n')
print(target.name+' · '+str(target.stat().st_size)+' bytes · '+str(len(paths))+' archivos públicos')

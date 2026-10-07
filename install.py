#!/usr/bin/env python3
"""Install code only. Provider profiles are never part of the package."""
import argparse
import os
from pathlib import Path
import sys

if sys.version_info < (3, 11):
    raise SystemExit('AI Command requiere Python 3.11 o posterior')

from ai_manager.core import ManagerError
from ai_manager.distribution import install

parser = argparse.ArgumentParser()
parser.add_argument('--prefix', default='/usr/local' if os.getuid() == 0 else str(Path.home() / '.local'))
parser.add_argument('--no-auto', action='store_true', help='No instalar los comandos codex/claude automáticos')
args = parser.parse_args()
try:
    result = install(Path(__file__).resolve().parent, args.prefix, not args.no_auto)
    print('AI Command ' + result['version'] + ' instalado en ' + args.prefix)
    print('Añade ' + str(Path(args.prefix) / 'bin') + ' al principio de PATH si aún no está.')
except (ManagerError, OSError) as exc:
    raise SystemExit('Instalación cancelada: ' + str(exc))

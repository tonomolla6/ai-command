"""Bare interactive provider commands use ai; native arguments pass through."""
import os
import sys

from .core import ManagerError
from .providers import executable


def provider_main(provider, argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    managed_flags = {'--ai-new': '--new', '--ai-dry-run': '--dry-run'}
    resume_command=bool(argv) and argv[0]=='resume'
    managed = (not argv and sys.stdin.isatty() and sys.stdout.isatty()) or (
        bool(argv) and argv[0] in managed_flags) or resume_command
    # An interactive child from an already bound account keeps its identity.
    if os.environ.get('AI_MANAGER_BOUND_PROVIDER') == provider and not argv:
        managed = False
    try:
        if not managed:
            path = executable(provider)
            os.execv(path, [path, *argv])
        else:
            options = []
            if resume_command:
                argv.pop(0);options.append('--resume')
                if argv and not argv[0].startswith('-'):
                    options.extend(['--session',argv.pop(0)])
            while argv and argv[0] in managed_flags:
                options.append(managed_flags[argv.pop(0)])
            from .cli import main
            return main(['auto', provider, *options, '--', *argv])
    except ManagerError as exc:
        print('ai: ' + str(exc), file=sys.stderr)
        return 1
    except OSError:
        print('ai: no se pudo ejecutar el CLI original.', file=sys.stderr)
        return 1

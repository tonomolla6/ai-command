"""Bare interactive provider commands use ai; native arguments pass through."""
import os
import sys

from .core import Manager, ManagerError
from .providers import executable
from .permissions import claude_arguments, claude_danger_enabled, is_claude_launch, single_tool_arguments


def single_provider_main(provider, argv=None):
    """Pass native arguments through, adding only an explicitly enabled policy."""
    argv=list(sys.argv[1:] if argv is None else argv)
    try:
        path=executable(provider)
        arguments=single_tool_arguments(Manager(),provider,argv)
        env=dict(os.environ)
        if provider=='agy':env['AGY_CLI_DISABLE_AUTO_UPDATE']='1'
        os.execve(path,[path,*arguments],env)
    except (ManagerError,OSError):
        print('ai: no se pudo ejecutar el CLI original de '+provider+'.',file=sys.stderr)
        return 1


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
            if provider == 'claude' and is_claude_launch(argv) and claude_danger_enabled():
                env = dict(os.environ);env['IS_SANDBOX'] = '1'
                os.execve(path, [path, *claude_arguments(argv)], env)
            else:
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

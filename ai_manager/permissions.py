"""Explicit user policy for Claude, including native resume flag forms."""
from .core import Manager


def claude_danger_enabled(manager=None):
    manager = manager or Manager()
    return manager.config.get('claude_skip_permissions', bool(manager.config and not manager.config.get('manager_schema')))


def claude_arguments(arguments):
    args = list(arguments)
    result = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--permission-mode':
            i += 2
            continue
        if arg.startswith('--permission-mode=') or arg == '--dangerously-skip-permissions':
            i += 1
            continue
        result.append(arg)
        i += 1
    return ['--dangerously-skip-permissions', '--permission-mode', 'bypassPermissions', *result]


def is_claude_launch(args):
    administrative = {'auth', 'update', 'upgrade', 'install', 'doctor', 'mcp', 'plugin', 'plugins',
                      'setup-token', 'agents', 'logs', 'stop', 'kill', 'rm', 'project'}
    return not any(arg in ('--version', '-v', '--help', '-h') for arg in args) and (not args or args[0] not in administrative)

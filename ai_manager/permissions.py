"""Explicit per-user permission policies for managed and native launches."""
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


TOOL_FLAGS = {'agy': '--dangerously-skip-permissions', 'opencode': '--auto'}
TOOL_ADMIN = {
    'agy': {'agent', 'agents', 'changelog', 'help', 'install', 'mcp', 'mic-serve',
            'models', 'plugin', 'plugins', 'remote-control', 'update'},
    'opencode': {'completion', 'acp', 'mcp', 'attach', 'debug', 'providers', 'auth',
                 'agent', 'upgrade', 'uninstall', 'serve', 'web', 'models', 'stats',
                 'export', 'import', 'github', 'pr', 'session', 'plugin', 'plug', 'db'},
}
TOOL_VALUES = {'--model', '-m', '--agent', '--prompt', '--prompt-interactive',
               '--conversation', '--session', '-s', '--project', '--add-dir',
               '--effort', '--mode', '--print', '-p', '--input-format', '--output-format',
               '--json-schema', '--log-file', '--print-timeout', '--log-level',
               '--hostname', '--port', '--cors', '--mdns-domain', '--replay-limit',
               '--command', '--format', '--file', '-f', '--title', '--attach',
               '--password', '--username', '-u', '--dir', '--variant'}


def single_tool_arguments(manager, provider, arguments):
    """Add the installed providers' official opt-in flag, preserving admin calls."""
    args = list(arguments)
    if not manager.config.get(provider + '_skip_permissions', False):
        return args
    first = None
    options = []
    i = 0
    flag = TOOL_FLAGS[provider]
    while i < len(args):
        arg = args[i]
        if arg == '--':
            options.extend(args[i:])
            break
        if arg in ('--help', '-h', '--version', '-v'):
            return args
        if arg in TOOL_VALUES:
            options.extend(args[i:i + 2])
            i += 2
            continue
        if arg == flag or arg.startswith(flag + '=') or (provider == 'opencode' and arg == '--no-auto'):
            i += 1
            continue
        if not arg.startswith('-') and first is None:
            first = arg
        options.append(arg)
        i += 1
    if first in TOOL_ADMIN[provider]:
        return args
    # Both the default TUI and run explicitly support OpenCode --auto.
    return [flag, *options]

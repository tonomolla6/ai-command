"""Explicit per-user permission policies for managed and native launches."""
from .core import Manager


CODEX_DANGER_FLAG = '--dangerously-bypass-approvals-and-sandbox'
CODEX_ADMIN = {'agents', 'login', 'logout', 'app-server', 'remote-control', 'mcp',
               'mcp-server', 'completion', 'features', 'debug', 'help', 'update',
               'upgrade', 'install', 'doctor', 'auth', 'plugin', 'plugins',
               'sandbox', 'apply', 'a', 'queue', 'archive', 'delete', 'unarchive',
               'migrate-rollouts', 'execpolicy', 'exec-server', 'cloud', 'proto',
               'responses-api-proxy', 'stdio-to-uds'}
CODEX_VALUES = {'-c', '--config', '-m', '--model', '-p', '--profile', '-C', '--cd',
                '-i', '--image', '--add-dir', '--local-provider', '--enable',
                '--disable', '--output-schema', '-o', '--output-last-message',
                '--color', '--remote', '--remote-auth-token-env', '--thread-source',
                '--cyber-access-program', '--base', '--commit', '--title',
                '--environment-id', '--name', '-s', '--sandbox', '-a', '--ask-for-approval'}


def is_codex_launch(arguments):
    """Classify native launches without interpreting option values as commands."""
    first = None
    i = 0
    args = list(arguments)
    while i < len(args):
        arg = args[i]
        if arg == '--':
            break
        if arg in ('--help', '-h', '--version', '-V'):
            return False
        if arg in CODEX_VALUES:
            i += 2
            continue
        if not arg.startswith('-') and first is None:
            first = arg
        i += 1
    return first not in CODEX_ADMIN


def codex_arguments(manager, arguments):
    """Use Codex's official full-access switch for explicitly enabled launches."""
    args = list(arguments)
    if not manager.config.get('codex_skip_permissions', False) or not is_codex_launch(args):
        return args
    result = []
    i = 0
    conflicting_values = {'-s', '--sandbox', '-a', '--ask-for-approval'}
    conflicting_flags = {CODEX_DANGER_FLAG, '--yolo', '--full-auto', '--approve-for-me'}
    while i < len(args):
        arg = args[i]
        if arg == '--':
            result.extend(args[i:])
            break
        if arg in conflicting_values:
            i += 2
            continue
        if (arg.split('=', 1)[0] in conflicting_flags or
                arg.startswith(('--sandbox=', '--ask-for-approval=')) or
                (len(arg) > 2 and arg[:2] in ('-s', '-a') and not arg.startswith('--'))):
            i += 1
            continue
        if arg in CODEX_VALUES:
            result.extend(args[i:i + 2])
            i += 2
            continue
        result.append(arg)
        i += 1
    return [CODEX_DANGER_FLAG, *result]


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
        if provider == 'agy' and (arg == '--sandbox' or arg.startswith('--sandbox=')):
            i += 1
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

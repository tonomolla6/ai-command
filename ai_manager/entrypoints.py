"""Bare interactive provider commands use ai; native arguments pass through."""
import os
import sys
import json

from .core import Manager, ManagerError
from .providers import executable
from .permissions import claude_arguments, claude_danger_enabled, is_claude_launch, single_tool_arguments
from .automatic import account_override


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
    from .sessions import claude_resume_request, shared_claude_arguments
    native_resume, native_session, native_extra = claude_resume_request(argv) if provider=='claude' else (False,None,argv)
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not any(a in ('-p','--print') for a in argv)
    managed = (not argv and sys.stdin.isatty() and sys.stdout.isatty()) or (
        bool(argv) and argv[0] in managed_flags) or resume_command or (native_resume and interactive)
    # An interactive child from an already bound account keeps its identity.
    if os.environ.get('AI_MANAGER_BOUND_PROVIDER') == provider and not argv:
        managed = False
    try:
        if not managed:
            path = executable(provider)
            admin = {'login','logout','app-server','mcp','mcp-server','completion','features',
                     'debug','help','update','upgrade','install','doctor','auth','plugin','plugins'}
            launch = is_claude_launch(argv) if provider=='claude' else (
                not any(arg in ('--version','-V','--help','-h') for arg in argv)
                and (not argv or argv[0] not in admin))
            manager=Manager() if os.environ.get('ACCOUNT','').strip() and launch else None
            account=account_override(manager,provider) if manager else None
            if account:
                from .registry import sync_identity, fixed_model_arguments
                if not manager.has_auth(account):
                    raise ManagerError(f"{account['label']} necesita login: ai login {provider} {account['account']}")
                if not sync_identity(manager,account):
                    raise ManagerError('No se pudo verificar la identidad de ACCOUNT')
                env=manager.env(account);env.pop('ACCOUNT',None)
                env['AI_MANAGER_BOUND_PROVIDER']=provider
                if provider=='codex':
                    options=['--no-daemon']
                    if account.get('shared_sqlite_home'):
                        options+=['-c','sqlite_home='+json.dumps(account['shared_sqlite_home'])]
                    os.execve(path,[path,*options,*fixed_model_arguments(account,argv),*argv],env)
                else:
                    from .claude_setup import repair_onboarding
                    repair_onboarding(manager,account)
                    argv=shared_claude_arguments(manager,argv)
                    env['CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN']='1'
                    if claude_danger_enabled(manager):
                        env['IS_SANDBOX']='1';argv=claude_arguments(argv)
                    os.execve(path,[path,*argv],env)
                return 0
            if provider == 'claude' and is_claude_launch(argv) and claude_danger_enabled():
                env = dict(os.environ);env['IS_SANDBOX'] = '1'
                env['CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN']='1'
                if native_resume and native_session:argv=shared_claude_arguments(Manager(),argv)
                os.execve(path, [path, *claude_arguments(argv)], env)
            else:
                os.execv(path, [path, *argv])
        else:
            options = []
            if resume_command:
                argv.pop(0);options.append('--resume')
                if argv and not argv[0].startswith('-'):
                    options.extend(['--session',argv.pop(0)])
            elif native_resume and interactive:
                argv=native_extra;options.append('--resume')
                if native_session:options.extend(['--session',native_session])
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

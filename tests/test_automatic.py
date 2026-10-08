import argparse
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_manager.automatic import refresh_accounts, select_account, quota_expiry, collect_limits, account_override
from ai_manager.cli import auto_launch
from ai_manager.core import Manager, ManagerError, now, write_json
from ai_manager.entrypoints import provider_main
from ai_manager.providers import claude_reset_at, executable, query_limits
from ai_manager.setup import install_auto_commands


def quota(account, *values, verified=True, status='OK'):
    return {'provider': account['provider'], 'account': account['account'],
            'email': account['email'], 'email_verified': verified, 'status': status,
            'queried_at': now(), 'windows': [{'name': name, 'available_percent': value}
                                           for name, value in zip(('5h', 'weekly'), values)],
            'credits': [{'balance': '99999', 'has_credits': True}]}


class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        # A failing mock assertion must never format the host's environment.
        self.environment = patch.dict(os.environ, {'PATH': os.defpath, 'HOME': str(self.home)}, clear=True)
        self.environment.start()
        self.manager = Manager(self.home)
        self.manager.config_dir.mkdir(parents=True)
        self.manager.state.mkdir(parents=True)
        accounts = []
        for provider, count in (('codex', 4), ('claude', 2)):
            for number in range(1, count + 1):
                home = self.home / f'.{provider}-account-{number}'
                home.mkdir()
                a = {'provider': provider, 'account': str(number), 'home': str(home),
                     'profile': str(home), 'label': f'{provider} {number}',
                     'email': f'{provider}{number}@example.invalid', 'enabled': number != 4}
                if provider == 'codex': a['shared_sqlite_home'] = str(self.home / '.codex')
                accounts.append(a)
                if number != 3:
                    write_json(home / ('auth.json' if provider == 'codex' else '.credentials.json'),
                               {'tokens': {'access_token': 'test-only'}} if provider == 'codex' else
                               {'claudeAiOauth': {'accessToken': 'test-only'}})
        self.manager.config = {'schema': 1, 'accounts': accounts}
        write_json(self.manager.config_path, self.manager.config)
        self.first = self.manager.account('codex', '1')
        self.second = self.manager.account('codex', '2')

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_rank_uses_every_window_not_credit_balance_or_summary(self):
        entries = {'codex:1': quota(self.first, 95, 10), 'codex:2': quota(self.second, 55, 60)}
        entries['codex:1']['available_percent'] = 100  # untrusted convenience field
        self.assertEqual(select_account(self.manager, 'codex', entries), self.second)
        entries['codex:2']['windows'][0]['available_percent'] = 0
        self.assertEqual(select_account(self.manager, 'codex', entries), self.first)
        entries['codex:1']['windows'][1]['available_percent'] = 0
        with self.assertRaises(ManagerError): select_account(self.manager, 'codex', entries)

    def test_excludes_unknown_partial_invalid_and_unverified_quota(self):
        for value in (None, 0, -1, 101, float('nan'), True):
            entries = {'codex:1': quota(self.first, 90, value),
                       'codex:2': quota(self.second, 100, 100, verified=False)}
            with self.subTest(value=value), self.assertRaises(ManagerError):
                select_account(self.manager, 'codex', entries)
        entry = quota(self.first, 100, 100)
        entry['email'] = 'different@example.invalid'
        with self.assertRaises(ManagerError): select_account(self.manager, 'codex', {'codex:1': entry})

    def test_nearest_reset_precedes_headroom_and_exhausted_accounts_stay_excluded(self):
        reference = dt.datetime.now(dt.timezone.utc)
        entries = {'codex:1': quota(self.first, 95, 95), 'codex:2': quota(self.second, 20, 30)}
        for key,hours in [('codex:1',4),('codex:2',1)]:
            entries[key]['windows'][0]['reset_at'] = (reference+dt.timedelta(hours=hours)).isoformat()
        self.assertEqual(select_account(self.manager, 'codex', entries), self.second)
        entries['codex:2']['windows'][1]['available_percent'] = 0
        self.assertEqual(select_account(self.manager, 'codex', entries), self.first)
        # A larger headroom with no known date does not outrank a known reset.
        entries['codex:2'] = quota(self.second, 100, 100)
        self.assertEqual(select_account(self.manager, 'codex', entries), self.first)
        # Past/unknown reset dates never become fabricated future resets.
        entries['codex:1']['windows'][0]['reset_at'] = (reference-dt.timedelta(hours=1)).isoformat()
        self.assertIsNone(quota_expiry(entries['codex:1']))
        self.assertEqual(select_account(self.manager, 'codex', entries), self.second)

    def test_reset_ties_use_headroom_then_account_number(self):
        reset = (dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1)).isoformat()
        entries = {'codex:1': quota(self.first, 10, 50), 'codex:2': quota(self.second, 40, 50)}
        for entry in entries.values(): entry['windows'][0]['reset_at'] = reset
        self.assertEqual(select_account(self.manager, 'codex', entries), self.second)
        entries['codex:1']['windows'][0]['available_percent'] = 40
        self.assertEqual(select_account(self.manager, 'codex', entries), self.first)

    def test_low_priority_is_last_resort_even_with_earlier_reset(self):
        self.first['priority'] = 'low'
        reference = dt.datetime.now(dt.timezone.utc)
        entries = {'codex:1': quota(self.first, 95, 95),
                   'codex:2': quota(self.second, 20, 30)}
        for key, hours in [('codex:1', 1), ('codex:2', 4)]:
            entries[key]['windows'][0]['reset_at'] = (reference + dt.timedelta(hours=hours)).isoformat()
        self.assertEqual(select_account(self.manager, 'codex', entries), self.second)
        entries['codex:2']['windows'][1]['available_percent'] = 0
        self.assertEqual(select_account(self.manager, 'codex', entries), self.first)
        entries['codex:1']['windows'][0]['available_percent'] = 0
        with self.assertRaises(ManagerError):
            select_account(self.manager, 'codex', entries)

    def test_account_environment_resolves_registered_aliases_and_rejects_wrong_provider(self):
        for value in ('codex2','x2','2',self.second['email']):
            with self.subTest(value=value),patch.dict(os.environ,{'ACCOUNT':value}):
                self.assertEqual(account_override(self.manager,'codex'),self.second)
        for value in ('claude2','c2','codex4','x999','../../auth.json'):
            with self.subTest(value=value),patch.dict(os.environ,{'ACCOUNT':value}),self.assertRaises(ManagerError):
                account_override(self.manager,'codex')
        with patch.dict(os.environ,{'ACCOUNT':'c2'}):
            self.assertEqual(account_override(self.manager,'claude')['account'],'2')

    def test_account_override_skips_quotas_and_preserves_explicit_resume(self):
        self.second['automatic']=False
        self.second['priority']='low'
        session={'id':'requested-thread','origin_account':'1','updated':1}
        for resume in (False,True):
            args=argparse.Namespace(provider='codex',new=not resume,resume=resume,
                                   session='requested-thread' if resume else None,dry_run=True,extra=[])
            with patch.dict(os.environ,{'ACCOUNT':'codex2'}), \
                 patch('ai_manager.cli.refresh_accounts') as refresh, \
                 patch('ai_manager.cli.list_sessions',return_value=[session]), \
                 patch('ai_manager.cli.launch',return_value=0) as launch:
                self.assertEqual(auto_launch(self.manager,args),0)
            refresh.assert_not_called()
            selected=launch.call_args.args[1]
            self.assertEqual(selected.account,'2')
            self.assertFalse(selected.automatic)
            self.assertEqual(launch.call_args.kwargs['session'],session if resume else None)

    def test_account_native_print_binds_home_and_enforces_claude_permissions(self):
        account=self.manager.account('claude','2')
        with patch.dict(os.environ,{'ACCOUNT':'claude2'}), \
             patch('ai_manager.entrypoints.Manager',return_value=self.manager), \
             patch('ai_manager.entrypoints.executable',return_value='/original/claude'), \
             patch('ai_manager.registry.sync_identity',return_value=account['email']), \
             patch('ai_manager.claude_setup.repair_onboarding'), \
             patch('ai_manager.entrypoints.os.execve') as run:
            self.assertEqual(provider_main('claude',['-p','test prompt']),0)
        env=run.call_args.args[2];arguments=run.call_args.args[1]
        self.assertEqual(env['CLAUDE_CONFIG_DIR'],account['home'])
        self.assertEqual(env['IS_SANDBOX'],'1')
        self.assertNotIn('ACCOUNT',env)
        self.assertEqual(arguments[-2:],['-p','test prompt'])
        self.assertIn('--dangerously-skip-permissions',arguments)

    def test_account_native_exec_binds_codex_home_and_preserves_arguments(self):
        with patch.dict(os.environ,{'ACCOUNT':'x2'}), \
             patch('ai_manager.entrypoints.Manager',return_value=self.manager), \
             patch('ai_manager.entrypoints.executable',return_value='/original/codex'), \
             patch('ai_manager.registry.sync_identity',return_value=self.second['email']), \
             patch('ai_manager.entrypoints.os.execve') as run:
            self.assertEqual(provider_main('codex',['exec','test prompt']),0)
        self.assertEqual(run.call_args.args[2]['CODEX_HOME'],self.second['home'])
        self.assertIn('--no-daemon',run.call_args.args[1])
        self.assertEqual(run.call_args.args[1][-2:],['exec','test prompt'])

    def test_reserved_free_account_never_enters_general_automatic_rotation(self):
        self.first.update(automatic=False,fixed_model='gpt-6-luna',plan_label='Free')
        entries={'codex:1':quota(self.first,100,100),'codex:2':quota(self.second,30,40)}
        self.assertEqual(select_account(self.manager,'codex',entries),self.second)
        with patch('ai_manager.automatic.query_limits',side_effect=lambda m,a:quota(a,30,40)) as read:
            self.assertEqual(set(refresh_accounts(self.manager,'codex')),{'codex:2'})
        self.assertEqual(read.call_count,1)
        self.assertEqual(read.call_args.args[1],self.second)

    def test_fresh_read_filters_active_authenticated_provider_accounts(self):
        self.manager.save_limits({'codex:1': quota(self.first, 100, 100)})
        def read(manager, account):
            return quota(account, status='UNKNOWN') if account['account'] == '1' else quota(account, 30, 40)
        with patch('ai_manager.automatic.query_limits', side_effect=read) as query:
            entries = refresh_accounts(self.manager, 'codex', force=True)
        self.assertEqual({call.args[1]['account'] for call in query.call_args_list}, {'1', '2'})
        self.assertEqual(query.call_count, 2)
        self.assertEqual(select_account(self.manager, 'codex', entries), self.second)
        self.assertEqual(entries['codex:1']['windows'], [])
        self.assertTrue(entries['codex:1']['previous_success']['windows'])
        self.assertEqual(self.manager.cache()['accounts']['codex:1']['status'], 'UNKNOWN')

    def test_six_usage_probes_start_together_instead_of_waiting_in_waves(self):
        accounts=[dict(self.first,account=str(n)) for n in range(6)]
        barrier=threading.Barrier(6,timeout=3)
        def read(manager,account):
            barrier.wait()
            return quota(account,70,80)
        with patch('ai_manager.automatic.query_limits',side_effect=read):
            entries=collect_limits(self.manager,accounts)
        self.assertEqual(len(entries),6)

    def test_stale_registry_identity_is_not_fresh_quota_verification(self):
        self.first['email_verified'] = True
        class RPC:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def call(self, method, params=None):
                if method == 'account/read': return {'account': None}
                return {'rateLimits': {'primary': {'usedPercent': 1, 'windowDurationMins': 300}}}
        with patch('ai_manager.providers.CodexRPC', return_value=RPC()):
            entry = query_limits(self.manager, self.first)
        self.assertFalse(entry['email_verified'])
        with self.assertRaises(ManagerError): select_account(self.manager, 'codex', {'codex:1': entry})

    def test_official_reset_count_is_preserved_without_redeeming_or_inferred_details(self):
        for count in (0,1,7,None,True,-1,'private-value'):
            calls=[]
            class RPC:
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def call(rpc,method,params=None):
                    calls.append(method)
                    if method=='account/read':return {'account':{'email':self.first['email']}}
                    return {'rateLimits':{'primary':{'usedPercent':100,'windowDurationMins':300}},
                            'rateLimitResetCredits':{'availableCount':count,'credits':[]}}
            with self.subTest(count=count),patch('ai_manager.providers.CodexRPC',return_value=RPC()):
                entry=query_limits(self.manager,self.first)
            self.assertEqual(calls,['account/read','account/rateLimits/read'])
            self.assertEqual(entry.get('reset_credits_available'),count if type(count) is int and count>=0 else None)
            self.assertNotIn('private-value',json.dumps(entry))
            self.assertEqual(entry['windows'][0]['available_percent'],0)
            with self.assertRaises(ManagerError):select_account(self.manager,'codex',{'codex:1':entry})

    def test_relaunch_selects_next_available_and_resumes_same_cwd_session(self):
        args = argparse.Namespace(provider='codex', new=False, resume=True, dry_run=True, extra=[])
        session = {'id': 'shared-thread', 'origin_account': '1', 'updated': 1, 'shared_sqlite': True}
        entries = {'codex:1': quota(self.first, 0, 80), 'codex:2': quota(self.second, 50, 70)}
        out = io.StringIO()
        with patch('ai_manager.cli.refresh_accounts', return_value=entries), \
             patch('ai_manager.cli.list_sessions', return_value=[session]) as sessions, \
             patch('ai_manager.cli.prepare_resume', return_value=['resume', 'shared-thread']), \
             patch('ai_manager.cli.executable', return_value='/original/codex'), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(auto_launch(self.manager, args), 0)
        plan = json.loads(out.getvalue())
        self.assertEqual(plan['account'], '2')
        self.assertEqual(plan['session_id'], 'shared-thread')
        self.assertEqual(plan['cwd'], str(Path.cwd()))
        self.assertEqual(plan['home'], self.second['home'])
        self.assertIn('resume', plan['command'])
        sessions.assert_called_once_with(self.manager, 'codex', Path.cwd())

    def test_new_session_and_provider_failure_never_rotate_or_retry(self):
        args = argparse.Namespace(provider='codex', new=True, dry_run=False, extra=['--', '--model', 'test'])
        entries = {'codex:1': quota(self.first, 70, 80), 'codex:2': quota(self.second, 20, 80)}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
             patch('ai_manager.cli.refresh_accounts', return_value=entries) as refresh, \
             patch('ai_manager.cli.list_sessions') as sessions, \
             patch('ai_manager.cli.sync_identity', return_value=self.first['email']), \
             patch('ai_manager.cli.handoff_prompt', return_value=None), \
             patch('ai_manager.cli.executable', return_value='/original/codex'), \
             patch('ai_manager.cli.sys.stdin.isatty', return_value=True), \
             patch('ai_manager.cli.sys.stdout.isatty', return_value=True), \
             patch('ai_manager.cli.subprocess.run', return_value=subprocess.CompletedProcess([], 42)) as run:
            self.assertEqual(auto_launch(self.manager, args), 42)
        refresh.assert_called_once()
        sessions.assert_not_called()
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs['cwd'], Path.cwd())
        self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'], self.first['home'])
        self.assertEqual(run.call_args.args[0][-2:], ['--model', 'test'])


    def test_default_auto_for_both_providers_never_reads_or_resumes_history(self):
        for provider in ('codex','claude'):
            account=self.manager.account(provider,'1')
            args=argparse.Namespace(provider=provider,new=False,dry_run=True,extra=[])
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()), \
                 patch('ai_manager.cli.refresh_accounts',return_value={self.manager.key(account):quota(account,80,90)}), \
                 patch('ai_manager.cli.list_sessions') as sessions,patch('ai_manager.cli.launch',return_value=0) as launch:
                auto_launch(self.manager,args)
            sessions.assert_not_called();self.assertFalse(launch.call_args.kwargs['resume'])
            self.assertIsNone(launch.call_args.kwargs['session'])

    def test_managed_claude_new_and_resume_use_requested_sandbox_and_permissions(self):
        from ai_manager.cli import launch
        from ai_manager.sessions import prepare_resume
        account=self.manager.account('claude','1')
        for session in (None,{'id':'fixture-claude','path':str(self.home/'transcript.jsonl')}):
            args=argparse.Namespace(provider='claude',account='1',dry_run=False,pick=False,extra=[])
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()), \
                 patch('ai_manager.cli.sync_identity',return_value=account['email']), \
                 patch('ai_manager.cli.repair_onboarding'),patch('ai_manager.cli.handoff_prompt',return_value=None), \
                 patch('ai_manager.cli.executable',return_value='/original/claude'), \
                 patch('ai_manager.cli.sys.stdin.isatty',return_value=True), \
                 patch('ai_manager.cli.sys.stdout.isatty',return_value=True), \
                 patch('ai_manager.cli.subprocess.run',return_value=subprocess.CompletedProcess([],0)) as run:
                self.assertEqual(launch(self.manager,args,resume=session is not None,session=session),0)
            self.assertEqual(run.call_count,1)
            self.assertIn('--dangerously-skip-permissions',run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['env']['IS_SANDBOX'],'1')
            self.assertEqual(run.call_args.kwargs['env']['CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN'],'1')
            self.assertEqual('--resume' in run.call_args.args[0],session is not None)

    def test_numbered_claude_native_resume_reads_another_accounts_transcript(self):
        from ai_manager.cli import launch
        ident='11111111-2222-3333-4444-555555555555'
        session={'id':ident,'path':str(self.home/'other-profile/transcript.jsonl')}
        args=argparse.Namespace(provider='claude',account='2',dry_run=True,extra=['--resume',ident])
        output=io.StringIO()
        with patch('ai_manager.cli.list_sessions',return_value=[session]), \
             patch('ai_manager.cli.handoff_prompt',return_value=None), \
             patch('ai_manager.cli.executable',return_value='/original/claude'),contextlib.redirect_stdout(output):
            launch(self.manager,args)
        plan=json.loads(output.getvalue())
        self.assertEqual(plan['home'],self.manager.account('claude','2')['home'])
        self.assertEqual(plan['session_id'],ident)
        self.assertIn(session['path'],plan['command'])
        self.assertNotIn(ident,plan['command'])
        self.assertIn('bypassPermissions',plan['command'])


class EntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'HOME': self.temp.name, 'PATH': os.defpath}, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_native_claude_uuid_resume_uses_shared_manager_with_remaining_options(self):
        ident='11111111-2222-3333-4444-555555555555'
        with patch('ai_manager.entrypoints.sys.stdin.isatty',return_value=True), \
             patch('ai_manager.entrypoints.sys.stdout.isatty',return_value=True), \
             patch('ai_manager.cli.main',return_value=0) as main:
            for flag in ('--resume','-r'):
                provider_main('claude',[flag,ident,'--model','example'])
                main.assert_called_with(['auto','claude','--resume','--session',ident,'--','--model','example'])

    def test_resume_parser_never_reads_prompts_as_resume_flags(self):
        from ai_manager.sessions import claude_resume_request
        for args in (['--','--continue'],['-p','--continue'],['--append-system-prompt','--continue']):
            self.assertEqual(claude_resume_request(args),(False,None,args))

    def test_resume_code_is_explicit_and_bare_commands_have_no_resume(self):
        with patch.dict(os.environ, {'AI_MANAGER_BOUND_PROVIDER': ''}), \
             patch('ai_manager.entrypoints.sys.stdin.isatty', return_value=True), \
             patch('ai_manager.entrypoints.sys.stdout.isatty', return_value=True), \
             patch('ai_manager.cli.main', return_value=0) as main:
            for provider in ('codex','claude'):
                provider_main(provider,[])
                main.assert_called_with(['auto',provider,'--'])
                provider_main(provider,['resume','11111111-2222-3333-4444-555555555555'])
                main.assert_called_with(['auto',provider,'--resume','--session','11111111-2222-3333-4444-555555555555','--'])

    def test_native_arguments_and_non_tty_do_not_select_or_change_environment(self):
        for provider, arguments in [('codex', ['--version']), ('codex', ['exec', 'test']),
                                    ('claude', ['auth', 'status']), ('claude', ['update']),
                                    ('claude', ['-p', 'test']), ('codex', [])]:
            with self.subTest(arguments=arguments), \
                 patch('ai_manager.entrypoints.claude_danger_enabled', return_value=False), \
                 patch('ai_manager.entrypoints.sys.stdin.isatty', return_value=False), \
                 patch('ai_manager.entrypoints.executable', return_value='/original/' + provider), \
                 patch('ai_manager.entrypoints.os.execv', side_effect=SystemExit(0)) as execute, \
                 patch('ai_manager.cli.main') as main:
                with self.assertRaises(SystemExit): provider_main(provider, arguments)
                execute.assert_called_once_with('/original/' + provider, ['/original/' + provider, *arguments])
                main.assert_not_called()

    def test_native_claude_resume_and_print_enforce_requested_danger_policy(self):
        for argv in (['--resume','session-id'],['-r','session-id'],['--continue'],['-p','example']):
            with patch('ai_manager.entrypoints.claude_danger_enabled',return_value=True), \
                 patch('ai_manager.entrypoints.executable',return_value='/original/claude'), \
                 patch('ai_manager.entrypoints.os.execve',side_effect=SystemExit(0)) as execute:
                with self.assertRaises(SystemExit):provider_main('claude',argv)
                args=execute.call_args.args
                self.assertEqual(args[1][1:4],['--dangerously-skip-permissions','--permission-mode','bypassPermissions'])
                self.assertEqual(args[1][4:],argv)
                self.assertEqual(args[2]['IS_SANDBOX'],'1')

    def test_bare_tty_and_namespaced_flags_use_manager(self):
        with patch.dict(os.environ, {'AI_MANAGER_BOUND_PROVIDER': ''}), \
             patch('ai_manager.entrypoints.sys.stdin.isatty', return_value=True), \
             patch('ai_manager.entrypoints.sys.stdout.isatty', return_value=True), \
             patch('ai_manager.cli.main', return_value=0) as main:
            self.assertEqual(provider_main('claude', []), 0)
            main.assert_called_with(['auto', 'claude', '--'])
            provider_main('codex', ['--ai-new', '--ai-dry-run', '--model', 'test'])
            main.assert_called_with(['auto', 'codex', '--new', '--dry-run', '--', '--model', 'test'])

    def test_installed_entrypoints_skip_recursion_preserve_native_binaries_and_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = home / 'original'; original.mkdir()
            wrappers = home / 'wrappers'
            for provider in ('codex', 'claude'):
                binary = original / provider
                binary.write_text('#!/bin/sh\nprintf "original-v1:%s\\n" "$1"\n'); binary.chmod(0o755)
            with patch.dict(os.environ, {'PATH': str(original)}), contextlib.redirect_stdout(io.StringIO()):
                install_auto_commands(Path(__file__).resolve().parent.parent, wrappers)
            with patch.dict(os.environ, {'PATH': str(wrappers) + os.pathsep + str(original)}):
                self.assertEqual(executable('codex'), str(original / 'codex'))
                for provider in ('codex', 'claude'):
                    result = subprocess.run([str(wrappers / provider), '--version'], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.stdout.strip(), 'original-v1:--version')
                    self.assertEqual(result.returncode, 0)
                (original / 'codex').write_text('#!/bin/sh\nprintf "original-v2\\n"\n')
                result = subprocess.run([str(wrappers / 'codex'), '--version'], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.stdout.strip(), 'original-v2')
                with contextlib.redirect_stdout(io.StringIO()):
                    install_auto_commands(Path(__file__).resolve().parent.parent, wrappers)
            (wrappers / 'claude').write_text('user-owned command')
            with patch.dict(os.environ, {'PATH': str(original)}), self.assertRaises(ManagerError):
                install_auto_commands(Path(__file__).resolve().parent.parent, wrappers)
            self.assertEqual((wrappers / 'claude').read_text(), 'user-owned command')


class ResetDateTests(unittest.TestCase):
    def test_claude_recognized_dates_timezone_midnight_and_new_year(self):
        for name,display,reference,expected in [
            ('Current session','2:59pm (Europe/Madrid)','2026-10-07T09:00:00+00:00','2026-10-07T12:59:00+00:00'),
            ('Current session','12am (Europe/Madrid)','2026-10-07T21:00:00+00:00','2026-10-07T22:00:00+00:00'),
            ('Current week (all models)','Oct13,7:59pm(Europe/Madrid)','2026-10-07T09:00:00+00:00','2026-10-13T17:59:00+00:00'),
            ('Current week (Fable)','Jan 2, 7:59pm (Europe/Madrid)','2026-12-30T20:00:00+00:00','2027-01-02T18:59:00+00:00'),
            ('Current week (all models)','Feb 29, 8pm (Europe/Madrid)','2028-02-27T09:00:00+00:00','2028-02-29T19:00:00+00:00')]:
            with self.subTest(display=display):
                self.assertEqual(claude_reset_at({'name':name,'reset_display':display},reference),expected)

    def test_claude_missing_timezone_unknown_weekday_and_ambiguous_clock_are_unknown(self):
        for name,display,reference in [
            ('Current session','2:59pm','2026-10-07T09:00:00+00:00'),
            ('Current week (all models)','2:59pm (Europe/Madrid)','2026-10-07T09:00:00+00:00'),
            ('Current week (all models)','Next Tuesday (Europe/Madrid)','2026-10-07T09:00:00+00:00'),
            ('Current session','2:30am (Europe/Madrid)','2026-10-24T23:00:00+00:00'),
            ('Current session','2:30am (Europe/Madrid)','2026-03-28T23:00:00+00:00'),
            ('Current week (all models)','Oct 13, 2:59pm (No/SuchZone)','2026-10-07T09:00:00+00:00')]:
            with self.subTest(display=display):
                self.assertIsNone(claude_reset_at({'name':name,'reset_display':display},reference))


if __name__ == '__main__': unittest.main()

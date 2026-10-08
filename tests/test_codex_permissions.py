import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.cli import launch, main
from ai_manager.core import Manager, read_json, write_json
from ai_manager.distribution import install
from ai_manager.permissions import CODEX_DANGER_FLAG, codex_arguments, is_codex_launch


class CodexPermissionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        self.environment = patch.dict(os.environ, {'HOME': str(self.home), 'PATH': os.defpath}, clear=True)
        self.environment.start()
        self.manager = Manager()
        self.manager.config_dir.mkdir(parents=True)
        self.manager.state.mkdir(parents=True)
        profile = self.home / '.codex-account-2'
        profile.mkdir()
        self.account = {'provider': 'codex', 'account': '2', 'label': 'Codex 2',
                        'email': 'user@example.invalid', 'email_verified': True,
                        'home': str(profile), 'shared_sqlite_home': str(profile),
                        'fixed_model': 'reserved-model'}
        self.manager.config = {'schema': 1, 'manager_schema': 1, 'accounts': [self.account],
                               'codex_skip_permissions': True, 'claude_skip_permissions': False}
        write_json(self.manager.config_path, self.manager.config)
        write_json(self.manager.credential(self.account), {'tokens': {'access_token': 'test-only'}})
        self.native = Path(self.temp.name) / 'native'
        self.native.mkdir()
        self.capture = Path(self.temp.name) / 'calls.json'
        self.fixture = self.native / 'codex'
        # The real installed launchers execute this native fixture. Its local
        # account/read protocol exercises identity verification without a login.
        self.fixture.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
from pathlib import Path
path = Path(os.environ['CAPTURE'])
calls = json.loads(path.read_text()) if path.exists() else []
calls.append({'args': sys.argv[1:], 'home': os.environ.get('CODEX_HOME'),
              'bound': os.environ.get('AI_MANAGER_BOUND_PROVIDER'),
              'account_env': os.environ.get('ACCOUNT'), 'cwd': os.getcwd()})
path.write_text(json.dumps(calls))
if sys.argv[1:2] == ['app-server']:
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request: continue
        result = {'account': {'email': 'user@example.invalid'}} if request['method'] == 'account/read' else {}
        print(json.dumps({'id': request['id'], 'result': result}), flush=True)
''')
        self.fixture.chmod(0o755)
        os.environ.update(PATH=str(self.native) + os.pathsep + os.defpath, CAPTURE=str(self.capture))

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_policy_is_opt_in_and_can_be_disabled(self):
        for value in (None, False):
            self.manager.config['codex_skip_permissions'] = value
            args = ['--sandbox', 'read-only', 'exec', 'task']
            self.assertEqual(codex_arguments(self.manager, args), args)

    def test_admin_and_option_values_remain_distinct_from_launches(self):
        for args in (['login', 'status'], ['app-server', '--listen', 'stdio://'],
                     ['--model', 'demo', 'doctor'], ['--config', 'model="demo"', 'mcp'],
                     ['--help'], ['resume', '--help'], ['exec', '--version'],
                     ['sandbox', 'linux', 'echo', 'test'], ['queue', 'thread', 'message']):
            with self.subTest(args=args):
                self.assertFalse(is_codex_launch(args))
                self.assertEqual(codex_arguments(self.manager, args), args)
        for args in ([], ['exec', 'task'], ['review', '--uncommitted'], ['fork', 'thread'],
                     ['--model', 'login'], ['--config', '--help'], ['--', '--help']):
            with self.subTest(args=args):
                self.assertTrue(is_codex_launch(args))
                self.assertEqual(codex_arguments(self.manager, args), [CODEX_DANGER_FLAG, *args])

    def test_conflicting_modes_are_removed_without_editing_prompt_or_config_values(self):
        args = ['--sandbox', 'read-only', '-sworkspace-write', '--sandbox=read-only',
                '-a', 'on-request', '-aon-request', '--ask-for-approval=on-request',
                '--approve-for-me', '--full-auto', '--yolo', CODEX_DANGER_FLAG,
                'resume', 'thread', '-m', '--sandbox', '-c', 'approval_policy="on-request"',
                '--', '--ask-for-approval=never']
        self.assertEqual(codex_arguments(self.manager, args),
                         [CODEX_DANGER_FLAG, 'resume', 'thread', '-m', '--sandbox',
                          '-c', 'approval_policy="on-request"', '--', '--ask-for-approval=never'])

    def test_real_native_launchers_cover_new_bound_resume_exec_and_review(self):
        prefix = Path(self.temp.name) / 'prefix'
        install(Path(__file__).resolve().parent.parent, prefix)
        env = dict(os.environ)
        for args in ([], ['exec', '--sandbox', 'read-only', 'task'],
                     ['exec', 'resume', 'thread', 'next task'], ['review', '--uncommitted']):
            with self.subTest(args=args):
                subprocess.run([prefix / 'bin/codex', *args], env=env, check=True, capture_output=True, timeout=10)
                call = read_json(self.capture)[-1]
                self.assertEqual(call['args'][0], CODEX_DANGER_FLAG)
                self.assertNotIn('--sandbox', call['args'])
        env['AI_MANAGER_BOUND_PROVIDER'] = 'codex'
        subprocess.run([prefix / 'bin/codex'], env=env, check=True, capture_output=True, timeout=10)
        self.assertEqual(read_json(self.capture)[-1]['bound'], 'codex')
        self.assertEqual(read_json(self.capture)[-1]['args'], [CODEX_DANGER_FLAG])
        for args in (['login', 'status'], ['--version'], ['exec', '--help'], ['resume', '--help']):
            subprocess.run([prefix / 'bin/codex', *args], env=env, check=True, capture_output=True, timeout=10)
            self.assertEqual(read_json(self.capture)[-1]['args'], args)

    def test_real_tty_new_and_explicit_resume_keep_account_and_session(self):
        prefix = Path(self.temp.name) / 'prefix'
        install(Path(__file__).resolve().parent.parent, prefix)
        ident = '11111111-2222-4333-8444-555555555555'
        sessions = Path(self.account['home']) / 'sessions'
        sessions.mkdir()
        (sessions / 'fixture.jsonl').write_text(json.dumps({'type': 'session_meta',
            'payload': {'id': ident, 'cwd': str(self.home)}}) + '\n')
        for args in ([], ['resume', ident]):
            master, slave = pty.openpty()
            try:
                subprocess.run([prefix / 'bin/codex', *args], env=dict(os.environ, ACCOUNT='codex2'),
                               stdin=slave, stdout=slave, stderr=slave, cwd=self.home,
                               timeout=10, check=True)
            finally:
                os.close(slave)
                os.close(master)
            call = read_json(self.capture)[-1]
            self.assertEqual(call['args'][0], CODEX_DANGER_FLAG)
            self.assertEqual(call['home'], self.account['home'])
            self.assertEqual(call['cwd'], str(self.home))
            self.assertIn('reserved-model', call['args'])
            if args:
                self.assertEqual(call['args'][call['args'].index('resume') + 1], ident)
            else:
                self.assertNotIn('resume', call['args'])

    def test_real_account_override_preserves_profile_model_and_read_only_probe(self):
        prefix = Path(self.temp.name) / 'prefix'
        install(Path(__file__).resolve().parent.parent, prefix)
        env = dict(os.environ, ACCOUNT='codex2')
        before = self.manager.credential(self.account).read_bytes()
        subprocess.run([prefix / 'bin/codex', 'exec', '--sandbox=read-only', 'task'],
                       env=env, check=True, capture_output=True, timeout=10)
        calls = read_json(self.capture)
        probe, agent = calls[-2:]
        self.assertEqual(probe['args'][0], 'app-server')
        self.assertNotIn(CODEX_DANGER_FLAG, probe['args'])
        self.assertEqual(agent['args'][0], CODEX_DANGER_FLAG)
        self.assertEqual(agent['home'], self.account['home'])
        self.assertIn('reserved-model', agent['args'])
        self.assertIsNone(agent['account_env'])
        self.assertEqual(self.manager.credential(self.account).read_bytes(), before)

    def test_managed_new_and_resume_execute_native_with_full_access(self):
        args = argparse.Namespace(provider='codex', account='2', dry_run=False, extra=['-a', 'on-request'])
        for session in (None, {'id': 'thread-fixture', 'path': str(self.home / 'fixture.jsonl')}):
            with contextlib.redirect_stdout(io.StringIO()), \
                 patch('ai_manager.cli.sys.stdin.isatty', return_value=True), \
                 patch('ai_manager.cli.sys.stdout.isatty', return_value=True), \
                 patch('ai_manager.cli.handoff_prompt', return_value=None):
                self.assertEqual(launch(self.manager, args, session=session, resume=session is not None), 0)
            calls = read_json(self.capture)
            self.assertNotIn(CODEX_DANGER_FLAG, calls[-2]['args'])
            self.assertEqual(calls[-1]['args'][0], CODEX_DANGER_FLAG)
            self.assertIn('--no-daemon', calls[-1]['args'])
            self.assertIn('--no-alt-screen', calls[-1]['args'])
            self.assertIn('reserved-model', calls[-1]['args'])
            self.assertNotIn('-a', calls[-1]['args'])
            if session:
                self.assertIn('resume', calls[-1]['args'])
                self.assertIn(session['id'], calls[-1]['args'])

    def test_configure_preserves_other_policies_and_backs_up_private_registry(self):
        original = read_json(self.manager.config_path)
        before = self.manager.credential(self.account).read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['configure', '--codex-danger', 'off']), 0)
        config = read_json(self.manager.config_path)
        self.assertFalse(config['codex_skip_permissions'])
        self.assertFalse(config['claude_skip_permissions'])
        self.assertEqual(config['accounts'], original['accounts'])
        backups = list((self.home / '.ai-manager/backups').glob('*/.config/ai-manager/config.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(read_json(backups[0]), original)
        self.assertEqual(self.manager.credential(self.account).read_bytes(), before)

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.claude_setup import repair_onboarding
from ai_manager.cli import login
from ai_manager.core import Manager, ManagerError, read_json, write_json
from ai_manager.providers import claude_usage, parse_claude_report, parse_claude


class AuthenticatedOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'PATH': os.defpath, 'HOME': str(self.home)}, clear=True)
        self.env.start()
        self.m = Manager(self.home)
        self.m.state.mkdir(parents=True)
        profile = self.home / '.claude-account-2'; profile.mkdir()
        self.account = {'provider': 'claude', 'account': '2', 'home': str(profile),
                        'email': 'claude2@example.invalid', 'label': 'Claude 2'}
        self.m.config = {'schema': 1, 'accounts': [self.account]}
        self.credential = profile / '.credentials.json'
        write_json(self.credential, {'claudeAiOauth': {'accessToken': 'fake-test-credential'}})
        self.before = self.credential.read_bytes()
        self.prefs = profile / '.claude.json'
        self.original = {'oauthAccount': {'emailAddress': self.account['email']},
                         'projects': {'/untrusted': {'hasTrustDialogAccepted': False}},
                         'bypassPermissionsModeAccepted': False, 'theme': 'dark'}
        write_json(self.prefs, self.original)
        self.auth = {'loggedIn': True, 'authMethod': 'claude.ai', 'email': self.account['email']}
        self.version = patch('ai_manager.claude_setup.version', return_value='2.1.284 (Claude Code)')
        self.version.start()

    def tearDown(self):
        self.version.stop(); self.env.stop(); self.temp.cleanup()

    def test_authenticated_first_run_repairs_only_two_preferences_with_backup(self):
        before = self.prefs.read_bytes()
        self.assertTrue(repair_onboarding(self.m, self.account, self.auth))
        expected = dict(self.original, hasCompletedOnboarding=True, lastOnboardingVersion='2.1.284')
        self.assertEqual(read_json(self.prefs), expected)
        self.assertEqual(self.credential.read_bytes(), self.before)

        backups = list((self.home / '.ai-manager/backups').glob('*/.claude-account-2/.claude.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        self.assertFalse(repair_onboarding(self.m, self.account, self.auth))
        self.assertEqual(len(list((self.home / '.ai-manager/backups').iterdir())), 1)

    def test_no_auth_wrong_identity_and_unknown_version_never_modify_preferences(self):
        before = self.prefs.read_bytes()
        for overrides in ({'loggedIn': False}, {'email': 'other@example.invalid'}, {'authMethod': 'api_key'}):
            with self.subTest(overrides=overrides), self.assertRaises(ManagerError):
                repair_onboarding(self.m, self.account, dict(self.auth, **overrides))
            self.assertEqual(self.prefs.read_bytes(), before)
        with patch('ai_manager.claude_setup.version', return_value='9.0.0'), self.assertRaises(ManagerError):
            repair_onboarding(self.m, self.account, self.auth)
        self.assertEqual(self.prefs.read_bytes(), before)
        self.credential.unlink()
        self.assertFalse(repair_onboarding(self.m, self.account, self.auth))
        self.assertEqual(self.prefs.read_bytes(), before)

    def test_corrupt_preferences_and_symlinks_are_preserved(self):
        self.prefs.write_text('{broken')
        with self.assertRaises(ManagerError):repair_onboarding(self.m, self.account, self.auth)
        self.assertEqual(self.prefs.read_text(), '{broken')
        other = self.home / 'other.json'; other.write_text('{}')
        self.prefs.unlink(); self.prefs.symlink_to(other)
        with self.assertRaises(ManagerError):repair_onboarding(self.m, self.account, self.auth)
        self.assertEqual(other.read_text(), '{}')

    def test_existing_login_command_repairs_without_reopening_oauth(self):
        args = argparse.Namespace(provider='claude', account='2', reauth=False)
        with patch('ai_manager.cli.sync_identity', return_value=self.account['email']), \
             patch('ai_manager.claude_setup.executable', return_value='/official/claude'), \
             patch('ai_manager.claude_setup.subprocess.run') as auth_status, \
             patch('ai_manager.cli.subprocess.call') as oauth, \
             contextlib.redirect_stdout(io.StringIO()):
            auth_status.return_value.returncode = 0
            auth_status.return_value.stdout = json.dumps(self.auth)
            self.assertEqual(login(self.m, args), 0)
        oauth.assert_not_called()
        self.assertTrue(read_json(self.prefs)['hasCompletedOnboarding'])
        self.assertEqual(self.credential.read_bytes(), self.before)


class OfficialUsageTests(unittest.TestCase):
    def test_installed_local_usage_capability_survives_a_cli_version_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Manager(tmp);m.state.mkdir(parents=True)
            binary=Path(tmp)/'claude'
            binary.write_bytes(b'ELF fixture\0{type:"local",name:"usage",aliases:["cost"],supportsNonInteractive:!0,load:()=>import("usage.js")}')
            account={'provider':'claude','home':str(Path(tmp)/'profile')}
            finish={'type':'result','subtype':'success','num_turns':0,'total_cost_usd':0}
            stream=json.dumps({'type':'assistant','usage_report':self.report()})+'\n'+json.dumps(finish)
            with patch('ai_manager.providers.version',return_value='2.1.293'), \
                 patch('ai_manager.providers.executable',return_value=str(binary)), \
                 patch('ai_manager.providers.subprocess.run') as run, \
                 patch('ai_manager.providers.claude_usage_pty',side_effect=AssertionError('trust dialog fallback')):
                run.return_value.returncode=0;run.return_value.stdout=stream
                self.assertEqual(claude_usage(m,account,details=True)['windows'][1]['available_percent'],0)

    def report(self):
        return {'rate_limits':{'limits':[
            {'kind':'session','percent':0,'resets_at':None,'is_active':False},
            {'kind':'weekly_all','percent':100,'resets_at':'2026-10-08T03:00:00+00:00'},
            {'kind':'weekly_scoped','percent':3.25,'scope':{'model':{'display_name':'Fable'}},
             'resets_at':'2026-10-08T03:00:00+00:00'}],
            'extra_usage':{'is_enabled':False,'monthly_limit':10000,'used_credits':0,'currency':'EUR'}}}

    def test_structured_usage_keeps_every_window_and_disabled_credits(self):
        result=parse_claude_report(self.report())
        self.assertEqual([w['available_percent'] for w in result['windows']],[100,0,96.8])
        self.assertEqual([w['window_minutes'] for w in result['windows']],[300,10080,10080])
        self.assertEqual(result['windows'][1]['reset_at'],'2026-10-08T03:00:00+00:00')
        self.assertIsNone(result['credits']['balance'])
        self.assertEqual(result['credits']['display'],'Desactivados')
        self.assertFalse(result['credits']['enabled'])

    def test_official_noninteractive_usage_works_without_a_trust_dialog(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Manager(tmp);m.state.mkdir(parents=True)
            account={'provider':'claude','home':str(Path(tmp)/'.claude-account-2')}
            finish={'type':'result','subtype':'success','num_turns':0,'total_cost_usd':0}
            stream=json.dumps({'type':'assistant','usage_report':self.report()})+'\n'+json.dumps(finish)
            with patch.dict(os.environ,{'PATH':os.defpath,'HOME':tmp},clear=True), \
                 patch('ai_manager.providers.version',return_value='2.1.284'), \
                 patch('ai_manager.providers.executable',return_value='/official/claude'), \
                 patch('ai_manager.providers.subprocess.run') as run, \
                 patch('ai_manager.providers.claude_usage_pty') as pty:
                run.return_value.returncode=0;run.return_value.stdout=stream
                result=claude_usage(m,account,details=True)
                self.assertEqual(result['windows'][1]['available_percent'],0)
                self.assertIn('--no-session-persistence',run.call_args.args[0])
                self.assertEqual(run.call_args.args[0][-1],'/usage')
                self.assertEqual(run.call_args.kwargs['cwd'],m.state)
                pty.assert_not_called()
                # Never silently retry or accept a result that consumed a turn.
                finish['num_turns']=1
                run.return_value.stdout=stream.splitlines()[0]+'\n'+json.dumps(finish)
                with self.assertRaises(ManagerError):claude_usage(m,account,details=True)
                self.assertEqual(run.call_count,2)
                pty.assert_not_called()

    def test_inline_text_fallback_does_not_include_percentages_in_window_names(self):
        rows=parse_claude('Current session: 0% used\nCurrent week (all models): 100% used · resets Oct 8, 5am (Europe/Madrid)')
        self.assertEqual([r['name'] for r in rows],['Current session','Current week (all models)'])
        self.assertEqual([r['available_percent'] for r in rows],[100,0])

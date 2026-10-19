"""Do not promote Claude's rate-limited fallback to a current quota."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.core import Manager, ManagerError, write_json
from ai_manager.providers import claude_usage, claude_usage_pty, parse_claude_report, query_limits


class ClaudeUsageFreshnessTests(unittest.TestCase):
    def run_usage(self, report, text):
        with tempfile.TemporaryDirectory() as tmp:
            manager = Manager(tmp)
            finish = {'type': 'result', 'subtype': 'success', 'num_turns': 0,
                      'total_cost_usd': 0, 'result': text}
            stream = json.dumps({'type': 'assistant', 'usage_report': report}) + '\n' + json.dumps(finish)
            with patch('ai_manager.providers.version', return_value='2.1.293'), \
                 patch('ai_manager.providers.claude_supports_usage', return_value=True), \
                 patch('ai_manager.providers.executable', return_value='/fixture/claude'), \
                 patch('ai_manager.providers.subprocess.run') as run:
                run.return_value.returncode = 0
                run.return_value.stdout = stream
                return claude_usage(manager, {'provider': 'claude', 'home': tmp}, details=True)

    def test_rate_limited_report_cannot_fall_back_to_stale_text(self):
        # 2.1.293 strips limits from seeded data but still prints old percentages.
        report = {'rate_limits': {'limits': None, 'extra_usage': {'is_enabled': False}}}
        with self.assertRaisesRegex(ManagerError, 'actual|antigu|caché'):
            self.run_usage(report, 'Current session: 34% used\nCurrent week (all models): 5% used')

    def test_explicit_last_known_warning_invalidates_even_recognizable_rows(self):
        report = {'rate_limits': {'limits': [{'kind': 'session', 'percent': 34}]}}
        for warning in ('Showing last-known usage (42 minutes ago) (rate limited — try again in a moment)',
                        'Could not refresh usage data',
                        'Usage endpoint is rate limited. Please try again in a moment.'):
            with self.subTest(warning=warning), self.assertRaises(ManagerError):
                self.run_usage(report, 'Current session: 34% used\n' + warning)

    def test_current_structured_report_still_preserves_every_percentage(self):
        report = {'rate_limits': {'limits': [
            {'kind': 'session', 'percent': 96, 'resets_at': '2026-10-19T15:40:00+00:00'},
            {'kind': 'weekly_all', 'percent': 13, 'resets_at': '2026-10-24T15:00:00+00:00'}]}}
        result = self.run_usage(report, 'Current session: 96% used\nCurrent week (all models): 13% used')
        self.assertEqual([w['available_percent'] for w in result['windows']], [4, 87])

    def test_legacy_structured_windows_are_not_discarded_for_stale_text(self):
        report = {'rate_limits': {
            'five_hour': {'utilization': 96, 'resets_at': '2026-10-19T15:40:00+00:00'},
            'seven_day': {'utilization': 13, 'resets_at': '2026-10-24T15:00:00+00:00'},
            'model_scoped': [{'display_name': 'Example', 'utilization': 20, 'resets_at': None}],
            'extra_usage': {'is_enabled': False}}}
        result = parse_claude_report(report)
        self.assertIsNotNone(result)
        self.assertEqual([w['available_percent'] for w in result['windows']], [4, 87, 80])

    def test_stale_usage_keeps_login_but_cannot_become_an_auto_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=Manager(tmp)
            profile=Path(tmp)/'profile';profile.mkdir()
            account={'provider':'claude','account':'1','home':str(profile),'email':'one@example.invalid'}
            write_json(profile/'.credentials.json',{'claudeAiOauth':{'accessToken':'fake-test-credential'}})
            auth={'loggedIn':True,'email':account['email']}
            with patch('ai_manager.providers.executable',return_value='/fixture/claude'), \
                 patch('ai_manager.providers.subprocess.run') as run, \
                 patch('ai_manager.claude_setup.repair_onboarding'), \
                 patch('ai_manager.providers.claude_usage',side_effect=ManagerError('Cuotas antiguas')):
                run.return_value.returncode=0;run.return_value.stdout=json.dumps(auth)
                result=query_limits(manager,account)
            self.assertEqual(result['status'],'UNKNOWN')
            self.assertTrue(result['email_verified'])
            self.assertEqual(result['windows'],[])
            self.assertIsNone(result['available_percent'])
            self.assertNotIn('last_success_at',result)
            self.assertTrue(manager.has_auth(account))

    def test_pty_rejects_visible_stale_warning_and_stops_only_its_child(self):
        # A real PTY emits the same bars/warning as Claude, without a provider call.
        original_popen=subprocess.Popen
        processes=[]
        fixture="import time;print('Current session: 34% used\\nShowing last-known usage (rate limited)',flush=True);time.sleep(60)"
        def launch_fixture(command,**kwargs):
            import sys
            process=original_popen([sys.executable,'-c',fixture],**kwargs)
            processes.append(process)
            return process
        with tempfile.TemporaryDirectory() as tmp:
            manager=Manager(tmp);manager.state.mkdir(parents=True)
            with patch('ai_manager.providers.executable',return_value='/fixture/claude'), \
                 patch('ai_manager.providers.subprocess.Popen',side_effect=launch_fixture):
                with self.assertRaisesRegex(ManagerError,'antigu|UNKNOWN'):
                    claude_usage_pty(manager,{'provider':'claude','home':tmp},timeout=4)
            self.assertEqual(len(processes),1)
            self.assertIsNotNone(processes[0].poll())


if __name__ == '__main__':
    unittest.main()

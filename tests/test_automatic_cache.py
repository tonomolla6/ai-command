import argparse
import contextlib
import datetime as dt
import io
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from ai_manager.automatic import refresh_accounts, select_account
from ai_manager.cli import auto_launch
from ai_manager.core import Manager, ManagerError, now, write_json


def observation(account, available=70):
    return {'provider': account['provider'], 'account': account['account'],
            'email': account['email'], 'email_verified': True, 'status': 'OK',
            'queried_at': now(), 'windows': [{'name': 'weekly', 'available_percent': available}]}


def simultaneous_launch(home, start, results, recheck_unavailable=False, available=70):
    """Independent processes exercise flock and the real atomic cache writes."""
    manager = Manager(home)
    def query(manager, account):
        fd = os.open(manager.state / 'probe-count', os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:os.write(fd, (account['account'] + '\n').encode())
        finally:os.close(fd)
        time.sleep(.3)
        return observation(account, available)
    try:
        start.wait(timeout=8)
        with patch('ai_manager.automatic.query_limits', side_effect=query):
            options = {'recheck_unavailable': True} if recheck_unavailable else {}
            rows = refresh_accounts(manager, 'claude', **options)
        results.put(sorted(rows))
    except Exception as exc:results.put(type(exc).__name__)


class AutomaticCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'HOME': str(self.home), 'PATH': os.defpath}, clear=True)
        self.environment.start()
        self.manager = Manager()
        self.manager.state.mkdir(parents=True)
        self.manager.config_dir.mkdir(parents=True)
        self.accounts = []
        for provider, number in [('claude', '2'), ('claude', '4'), ('codex', '2')]:
            profile = self.home / ('.' + provider + '-account-' + number)
            profile.mkdir()
            account = {'provider': provider, 'account': number, 'home': str(profile),
                       'label': provider + ' ' + number, 'email': provider + number + '@example.invalid'}
            self.accounts.append(account)
            write_json(self.manager.credential(account), {'tokens': {'access_token': 'test-only'}} if provider == 'codex'
                       else {'claudeAiOauth': {'accessToken': 'test-only'}})
        self.manager.config = {'schema': 1, 'manager_schema': 1, 'accounts': self.accounts}
        write_json(self.manager.config_path, self.manager.config)

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def seed(self):
        rows = {self.manager.key(a): observation(a) for a in self.accounts}
        self.manager.save_limits(rows)
        return rows

    def test_four_sequential_launches_reuse_usage_cache_for_both_providers(self):
        self.seed()
        with patch('ai_manager.automatic.query_limits') as query:
            for provider in ('claude', 'codex'):
                for _ in range(4):
                    notifications = []
                    rows = refresh_accounts(self.manager, provider, notify=notifications.append)
                    self.assertEqual(notifications, [True])
                    self.assertEqual(select_account(self.manager, provider, rows)['provider'], provider)
            query.assert_not_called()

    def test_only_expired_missing_and_reset_crossed_rows_are_queried(self):
        rows = self.seed()
        rows['claude:2']['queried_at'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=121)).isoformat()
        self.manager.save_limits(rows)
        with patch('ai_manager.automatic.query_limits', side_effect=lambda m, a: observation(a)) as query:
            refresh_accounts(self.manager, 'claude')
            self.assertEqual([c.args[1]['account'] for c in query.call_args_list], ['2'])
        rows = self.seed()
        rows['claude:4']['queried_at'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=20)).isoformat()
        rows['claude:4']['windows'][0].update(available_percent=0,
            reset_at=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)).isoformat())
        self.manager.save_limits(rows)
        with patch('ai_manager.automatic.query_limits', side_effect=lambda m, a: observation(a)) as query:
            refresh_accounts(self.manager, 'claude')
            self.assertEqual([c.args[1]['account'] for c in query.call_args_list], ['4'])

    def test_force_ignores_recent_cache_and_never_falls_back_to_old_availability(self):
        self.seed()
        def failed(manager, account):
            row = observation(account)
            row.update(status='UNKNOWN', email_verified=False, windows=[])
            return row
        with patch('ai_manager.automatic.query_limits', side_effect=failed) as query:
            rows = refresh_accounts(self.manager, 'claude', force=True)
            self.assertEqual(query.call_count, 2)
            with self.assertRaises(ManagerError):select_account(self.manager, 'claude', rows)
            query.reset_mock()
            rows = refresh_accounts(self.manager, 'claude')
            query.assert_not_called()
            with self.assertRaises(ManagerError):select_account(self.manager, 'claude', rows)
            self.assertEqual(rows['claude:2']['windows'], [])
            self.assertTrue(rows['claude:2']['previous_success']['windows'])

    def test_invalid_identity_time_and_new_login_invalidate_cached_rows(self):
        changes = [{'email': 'other@example.invalid'}, {'provider': 'codex'}, {'account': '99'},
                   {'queried_at': 'bad-date'}, {'queried_at': dt.datetime.now().isoformat()},
                   {'queried_at': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)).isoformat()},
                   {'status': 'SIN LOGIN'}]
        for change in changes:
            with self.subTest(change=change):
                rows = self.seed();rows['claude:2'].update(change);self.manager.save_limits(rows)
                with patch('ai_manager.automatic.query_limits', side_effect=lambda m, a: observation(a)) as query:
                    refresh_accounts(self.manager, 'claude')
                    self.assertEqual([c.args[1]['account'] for c in query.call_args_list], ['2'])

    def test_exhausted_and_unverified_cached_accounts_are_not_selected(self):
        rows = self.seed()
        rows['claude:2']['windows'][0]['available_percent'] = 0
        rows['claude:4']['email_verified'] = False
        self.manager.save_limits(rows)
        with patch('ai_manager.automatic.query_limits') as query:
            cached = refresh_accounts(self.manager, 'claude')
            query.assert_not_called()
        with self.assertRaises(ManagerError):select_account(self.manager, 'claude', cached)

    def test_four_simultaneous_processes_share_one_refresh(self):
        context = multiprocessing.get_context('fork')
        start = context.Barrier(4)
        results = context.Queue()
        processes = [context.Process(target=simultaneous_launch,
                     args=(str(self.home), start, results)) for _ in range(4)]
        try:
            for process in processes:process.start()
            for _ in processes:self.assertEqual(results.get(timeout=12), ['claude:2', 'claude:4'])
            for process in processes:
                process.join(timeout=5)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sorted((self.manager.state / 'probe-count').read_text().splitlines()), ['2', '4'])
        finally:
            for process in processes:
                if process.is_alive():process.terminate()
                process.join(timeout=5)
            results.close();results.join_thread()

    def test_resume_reuses_cache_and_reports_it_without_changing_exact_session(self):
        self.seed()
        args = argparse.Namespace(provider='claude', dry_run=True, resume=True,
                                  session='requested-thread', extra=[])
        session = {'id': args.session, 'path': str(self.home / 'fixture.jsonl')}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
             patch('ai_manager.automatic.query_limits') as query, \
             patch('ai_manager.cli.list_sessions', return_value=[session]), \
             patch('ai_manager.cli.handoff_prompt', return_value=None), \
             patch('ai_manager.cli.executable', return_value='/official/claude'):
            self.assertEqual(auto_launch(self.manager, args), 0)
        query.assert_not_called()
        self.assertIn('cuotas oficiales en caché', err.getvalue())
        self.assertNotIn('consultando cuotas', err.getvalue())
        self.assertEqual(json.loads(out.getvalue())['session_id'], args.session)

    def test_cached_exhaustion_rechecks_before_new_and_exact_resume_for_both_providers(self):
        for provider in ('codex', 'claude'):
            for resume in (False, True):
                with self.subTest(provider=provider, resume=resume):
                    rows = self.seed()
                    for row in rows.values():row['windows'][0]['available_percent'] = 0
                    self.manager.save_limits(rows)
                    args = argparse.Namespace(provider=provider, dry_run=True, resume=resume,
                                              session='requested-thread' if resume else None, extra=[])
                    session = {'id': 'requested-thread'}
                    with contextlib.redirect_stderr(io.StringIO()), \
                         patch('ai_manager.automatic.query_limits', side_effect=lambda m, a: observation(a)) as query, \
                         patch('ai_manager.cli.list_sessions', return_value=[session]) as sessions, \
                         patch('ai_manager.cli.launch', return_value=0) as launch:
                        self.assertEqual(auto_launch(self.manager, args), 0)
                    self.assertEqual(query.call_count, 1 if provider == 'codex' else 2)
                    self.assertEqual({call.args[1]['provider'] for call in query.call_args_list}, {provider})
                    self.assertEqual(launch.call_args.kwargs['session'], session if resume else None)
                    self.assertEqual(launch.call_args.kwargs['resume'], resume)
                    if not resume:sessions.assert_not_called()

    def test_no_second_query_when_observations_are_already_fresh_or_forced(self):
        for force in (False, True):
            with self.subTest(force=force):
                self.manager.save_limits({self.manager.key(a): observation(a, 0) for a in self.accounts})
                if not force:(self.manager.state / 'limits.json').unlink()
                with patch('ai_manager.automatic.query_limits', side_effect=lambda m, a: observation(a, 0)) as query:
                    rows = refresh_accounts(self.manager, 'claude', force=force, recheck_unavailable=True)
                self.assertEqual(query.call_count, 2)
                with self.assertRaises(ManagerError):select_account(self.manager, 'claude', rows)

    def test_partial_refresh_rechecks_only_other_cached_exhausted_accounts(self):
        rows = self.seed()
        rows['claude:2']['queried_at'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=121)).isoformat()
        rows['claude:4']['windows'][0]['available_percent'] = 0
        self.manager.save_limits(rows)
        with patch('ai_manager.automatic.query_limits',
                   side_effect=lambda m, a: observation(a, 0 if a['account'] == '2' else 70)) as query:
            rows = refresh_accounts(self.manager, 'claude', recheck_unavailable=True)
        self.assertEqual([call.args[1]['account'] for call in query.call_args_list], ['2', '4'])
        self.assertEqual(select_account(self.manager, 'claude', rows)['account'], '4')

    def test_unknown_or_wrong_identity_after_recheck_never_launches_or_uses_previous_success(self):
        for change in ({'status': 'UNKNOWN', 'windows': []}, {'email': 'other@example.invalid'}):
            with self.subTest(change=change):
                rows = self.seed()
                for row in rows.values():row['windows'][0]['available_percent'] = 0
                self.manager.save_limits(rows)
                def query(manager, account):
                    row = observation(account)
                    row.update(change)
                    return row
                args = argparse.Namespace(provider='claude', dry_run=True, resume=True,
                                          session='requested-thread', extra=[])
                with contextlib.redirect_stderr(io.StringIO()), \
                     patch('ai_manager.automatic.query_limits', side_effect=query) as read, \
                     patch('ai_manager.cli.launch') as launch, self.assertRaises(ManagerError):
                    auto_launch(self.manager, args)
                self.assertEqual(read.call_count, 2)
                launch.assert_not_called()

    def test_recheck_stays_inside_authenticated_enabled_automatic_provider_pool(self):
        rows = self.seed()
        for row in rows.values():row['windows'][0]['available_percent'] = 0
        self.manager.save_limits(rows)
        for account in self.accounts:
            if account['provider'] == 'claude':
                account['automatic'] = False
        with patch('ai_manager.automatic.query_limits') as query:
            self.assertEqual(refresh_accounts(self.manager, 'claude', recheck_unavailable=True), {})
        query.assert_not_called()
        self.accounts[0]['automatic'] = True
        self.manager.credential(self.accounts[0]).unlink()
        with patch('ai_manager.automatic.query_limits') as query:
            self.assertEqual(refresh_accounts(self.manager, 'claude', recheck_unavailable=True), {})
        query.assert_not_called()

    def test_four_simultaneous_rechecks_share_one_query_even_when_still_exhausted(self):
        rows = self.seed()
        # Match even the second-resolution timestamp returned by the probe;
        # concurrent waiters must recognize a new observation with unchanged quota.
        for row in rows.values():row['windows'][0]['available_percent'] = 0
        self.manager.save_limits(rows)
        context = multiprocessing.get_context('fork')
        start = context.Barrier(4)
        results = context.Queue()
        processes = [context.Process(target=simultaneous_launch,
                     args=(str(self.home), start, results, True, 0)) for _ in range(4)]
        try:
            for process in processes:process.start()
            for _ in processes:self.assertEqual(results.get(timeout=12), ['claude:2', 'claude:4'])
            for process in processes:
                process.join(timeout=5)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sorted((self.manager.state / 'probe-count').read_text().splitlines()), ['2', '4'])
        finally:
            for process in processes:
                if process.is_alive():process.terminate()
                process.join(timeout=5)
            results.close();results.join_thread()

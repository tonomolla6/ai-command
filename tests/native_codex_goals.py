#!/usr/bin/env python3
"""Exercise the installed native goal API in disposable profiles, with zero turns.

Run: python3 tests/native_codex_goals.py --codex /path/to/original/codex
No login is copied or required; temporary goals are paused and deleted afterward.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai_manager.core import Manager, ManagerError
from ai_manager.history_imports import imported_resume
from ai_manager.providers import CodexRPC


def verify(codex):
    with tempfile.TemporaryDirectory(prefix='ai-native-goal-check-') as tmp:
        home = Path(tmp)
        home.chmod(0o700)
        manager = Manager(home)
        manager.state.mkdir(parents=True, mode=0o700)
        shared = home / '.codex'
        shared.mkdir(mode=0o700)
        accounts = [{'provider': 'codex', 'account': number,
                     'home': str(home / ('profile-' + number)),
                     'shared_sqlite_home': str(shared)} for number in ('1', '2', '3')]
        for account in accounts:
            Path(account['home']).mkdir(mode=0o700)
        manager.config = {'accounts': accounts}
        with patch('ai_manager.providers.executable', return_value=str(codex)):
            with CodexRPC(manager, accounts[0], timeout=35) as rpc:
                result = rpc.call('thread/start', {'cwd': str(home),
                                  'approvalPolicy': 'never', 'sandbox': 'read-only'})
                ident = result['thread']['id']
                goal = rpc.call('thread/goal/set', {'threadId': ident, 'status': 'paused',
                               'objective': 'Verify native persistence without any model turns.',
                               'tokenBudget': 100000})['goal']
                fields = ('threadId', 'objective', 'status', 'tokenBudget',
                          'tokensUsed', 'timeUsedSeconds', 'createdAt')
                original = {field: goal.get(field) for field in fields}
                native = rpc.call('thread/read', {'threadId': ident, 'includeTurns': False})
                transcript = native['thread']['path']
            wrong = manager.state / 'restored/codex-index'
            wrong.mkdir(parents=True, mode=0o700)
            # The same native transcript is discoverable in its profile, but the
            # alternate sqlite_home has no goal. No conversation is duplicated.
            with CodexRPC(manager, dict(accounts[0], shared_sqlite_home=str(wrong)), timeout=35) as rpc:
                assert rpc.call('thread/goal/get', {'threadId': ident})['goal'] is None
                try:
                    rpc.call('thread/goal/set', {'threadId': ident, 'status': 'paused'})
                except ManagerError as error:
                    assert 'error -32600' in str(error), str(error)
                else:
                    raise AssertionError('An empty index unexpectedly accepted a goal update')
            print('PASS: a different index hides the goal and rejects its status update')
            relative = str(Path(transcript).relative_to(Path(accounts[0]['home']) / 'sessions'))
            snapshot = {'id': ident, 'provider': 'codex', 'relative_path': relative,
                        'snapshot_manifest': str(home / 'test-manifest.json'), 'updated': 0}
            for account in accounts:
                command = imported_resume(manager, account, snapshot, dry_run=True)
                selected = Path(json.loads(next(arg.split('=', 1)[1] for arg in command
                                                if arg.startswith('sqlite_home='))))
                assert selected == shared
                assert command[command.index('resume') + 1] == ident
                with CodexRPC(manager, dict(account, shared_sqlite_home=str(selected)), timeout=35) as rpc:
                    rpc.call('thread/resume', {'threadId': ident, 'cwd': str(home),
                             'approvalPolicy': 'never', 'sandbox': 'read-only'})
                    goal = rpc.call('thread/goal/set', {'threadId': ident, 'status': 'paused'})['goal']
                    assert {field: goal.get(field) for field in fields} == original
                print('PASS: profile ' + account['account'] + ' resumes the same goal, budget and counters')
            with CodexRPC(manager, accounts[0], timeout=35) as rpc:
                rpc.call('thread/delete', {'threadId': ident})
            assert not any(home.glob('**/auth.json'))
            print('PASS: zero turns, no credentials copied, disposable thread deleted')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', type=Path, required=True)
    args = parser.parse_args()
    verify(args.codex.absolute())

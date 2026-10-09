"""Native goal persistence must not change when an imported ID is resumed."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ai_manager.core import Manager, ManagerError
from ai_manager.history_imports import imported_resume
from ai_manager.sessions import list_sessions, prepare_resume


class CodexGoalRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.m = Manager(self.home)
        self.primary = self.home / '.codex'
        self.restored = self.m.state / 'restored/codex-index'
        self.ident = '11111111-2222-3333-4444-555555555555'
        self.path = self.primary / 'sessions/2026/10/09' / ('rollout-test-' + self.ident + '.jsonl')
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({'type': 'session_meta', 'payload': {
            'id': self.ident, 'cwd': str(self.home / 'repo')}}) + '\n')
        self.accounts = [{'provider': 'codex', 'account': number,
            'home': str(self.home / ('.codex-account-' + number)),
            'shared_sqlite_home': str(self.primary)} for number in ('2', '4')]
        self.m.config = {'accounts': self.accounts}
        self.snapshot = {'provider': 'codex', 'id': self.ident,
            'snapshot_manifest': str(self.home / 'manifest.json'),
            'relative_path': '2026/10/09/' + self.path.name, 'updated': 10}
        for directory in (self.primary, self.restored):
            self.index(directory, self.path)

    def index(self, directory, path, updated=100):
        directory.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(directory / 'state_5.sqlite') as db:
            db.execute('create table if not exists threads '
                       '(id text primary key, rollout_path text, cwd text, updated_at integer)')
            db.execute('insert or replace into threads values (?,?,?,?)',
                       (self.ident, str(path), str(self.home / 'repo'), updated))

    def goal(self, directory):
        with sqlite3.connect(directory / 'goals_1.sqlite') as db:
            db.execute('create table thread_goals (thread_id text primary key, objective text, '
                       'status text, tokens_used integer, time_used_seconds integer)')
            db.execute('insert into thread_goals values (?,?,?,?,?)',
                       (self.ident, 'Private task objective', 'usage_limited', 123456, 789))

    def selected_index(self, arguments):
        return Path(json.loads(next(v.split('=', 1)[1] for v in arguments
                                    if v.startswith('sqlite_home='))))

    def test_import_resume_keeps_existing_goal_in_primary_for_every_account(self):
        self.goal(self.primary)
        before = (self.primary / 'goals_1.sqlite').read_bytes()
        for account in self.accounts:
            command = imported_resume(self.m, account, self.snapshot, dry_run=True)
            self.assertEqual(self.selected_index(command), self.primary)
            self.assertEqual(command[command.index('resume') + 1], self.ident)
        self.assertEqual((self.primary / 'goals_1.sqlite').read_bytes(), before)
        self.assertFalse((self.restored / 'goals_1.sqlite').exists())

    def test_goal_created_in_restored_index_keeps_its_own_index(self):
        self.goal(self.restored)
        for account in self.accounts:
            self.assertEqual(self.selected_index(imported_resume(
                self.m, account, self.snapshot, dry_run=True)), self.restored)

    def test_different_transcripts_cannot_silently_switch_to_goal_index(self):
        self.goal(self.primary)
        other = self.path.with_name('rollout-other-' + self.ident + '.jsonl')
        other.write_bytes(self.path.read_bytes() + b'{"type":"different_history"}\n')
        self.index(self.restored, other)
        with self.assertRaisesRegex(ManagerError, 'goal|objetivo'):
            imported_resume(self.m, self.accounts[0], self.snapshot, dry_run=True)

    def test_conflicting_goals_are_not_silently_overwritten_or_merged(self):
        self.goal(self.primary)
        self.goal(self.restored)
        with self.assertRaisesRegex(ManagerError, 'goal|objetivo'):
            imported_resume(self.m, self.accounts[0], self.snapshot, dry_run=True)

    def test_restored_index_is_discovered_without_an_import_manifest(self):
        self.goal(self.restored)
        self.index(self.restored, self.path, updated=200)
        sessions = list_sessions(self.m, 'codex', self.home / 'repo')
        session = next(s for s in sessions if s['id'] == self.ident)
        for account in self.accounts:
            command = prepare_resume(self.m, account, session, dry_run=True)
            self.assertEqual(self.selected_index(command), self.restored)

    def test_dry_run_does_not_create_missing_state_or_read_goal_objectives(self):
        for directory in (self.primary, self.restored):
            (directory / 'state_5.sqlite').unlink()
        command = imported_resume(self.m, self.accounts[0], self.snapshot, dry_run=True)
        self.assertEqual(self.selected_index(command), self.restored)
        self.assertFalse(Path(self.accounts[0]['home']).exists())
        self.assertFalse(any(self.home.glob('**/goals_*.sqlite')))


if __name__ == '__main__':
    unittest.main()

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_manager.core import Manager, ManagerError, atomic_write, public_text, safe_file
from ai_manager.providers import Screen, parse_claude, parse_codex, codex_credits, claude_credits
from ai_manager.setup import clean_settings, toml_dump


class SecurityTests(unittest.TestCase):
    def test_probe_lock_waits_for_other_query_with_a_bounded_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            first=Manager(tmp);second=Manager(tmp);started=threading.Event();finished=threading.Event()
            errors=[]
            def wait_for_account():
                started.set()
                try:
                    with second.lock('same-account',timeout=1):finished.set()
                except Exception as error:errors.append(type(error).__name__)
            with first.lock('same-account'):
                with self.assertRaises(ManagerError):
                    with second.lock('same-account',timeout=0):pass
                thread=threading.Thread(target=wait_for_account);thread.start();started.wait(1)
                self.assertFalse(finished.is_set())
            thread.join(2)
            self.assertFalse(thread.is_alive());self.assertTrue(finished.is_set());self.assertEqual(errors,[])

    def test_secret_detection_and_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"conversation.jsonl"
            path.write_text('Discuss token limits, without actual credentials.\n')
            self.assertTrue(safe_file(path))
            secret='sk-ant-'+'a'*40
            path.write_text('hello '+secret)
            self.assertFalse(safe_file(path))
            self.assertNotIn(secret,public_text('before\n'+secret+'\nafter'))
            path.write_text('password="'+'z'*20+'"')
            self.assertFalse(safe_file(path))
            alias=Path(tmp)/"alias.jsonl";alias.symlink_to(path)
            self.assertFalse(safe_file(alias))

    def test_atomic_refuses_symlink_and_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"data.json"
            atomic_write(path,'{}')
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            alias=Path(tmp)/"alias";alias.symlink_to(path)
            with self.assertRaises(ManagerError):atomic_write(alias,'bad')
            self.assertEqual(path.read_text(),'{}')

    def test_account_env_keeps_workspace_and_removes_provider_auth(self):
        m=Manager('/tmp/example-ai-manager')
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'secret','CODEX_HOME':'wrong',
                                    'CLAUDE_CONFIG_DIR':'wrong','HOME':'/home/alice','GIT_AUTHOR_NAME':'Alice'}):
            env=m.env({'provider':'codex','home':'/home/alice/.codex-account-2'})
            self.assertNotIn('ANTHROPIC_API_KEY',env)
            self.assertNotIn('CLAUDE_CONFIG_DIR',env)
            self.assertEqual(env['CODEX_HOME'],'/home/alice/.codex-account-2')
            self.assertEqual(env['HOME'],'/home/alice')
            self.assertEqual(env['GIT_AUTHOR_NAME'],'Alice')

    def test_sanitized_settings_roundtrip_without_credentials(self):
        data={'model':'gpt-test','mcp_servers':{'private':{'http_headers':{'Authorization':'secret'}}},
              'env':{'ANTHROPIC_API_KEY':'secret'},'features':{'x':True},'projects':{'/a':{'trust_level':'trusted'}}}
        clean=clean_settings(data)
        self.assertNotIn('mcp_servers',clean)
        self.assertNotIn('env',clean)
        import tomllib
        self.assertEqual(tomllib.loads(toml_dump(clean)),clean)


class ParserTests(unittest.TestCase):
    def test_codex_multiple_buckets_and_windows(self):
        data={'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':25,'windowDurationMins':300,'resetsAt':1730947200},
                                                     'secondary':{'usedPercent':80,'windowDurationMins':10080}},
                                    'review':{'primary':{'usedPercent':5,'windowDurationMins':60}}}}
        rows=parse_codex(data)
        self.assertEqual([r['available_percent'] for r in rows],[75,20,95])
        self.assertEqual(rows[0]['name'],'codex/5h')
        self.assertTrue(rows[0]['reset_at'].endswith('+00:00'))

    def test_codex_unknown_never_invented(self):
        self.assertEqual(parse_codex({}),[])
        rows=parse_codex({'rateLimits':{'primary':{'usedPercent':200}}})
        self.assertIsNone(rows[0]['available_percent'])

    def test_claude_actual_and_reasonable_format_variants(self):
        for text in [
            'Current session\n████ 16% used\nResets 3pm (Europe/Madrid)\nCurrent week (all models)\n27% used\nResets Oct 13, 8pm\nCurrent week (Fable)\n2% used\nResets Oct 13, 8pm\nUsage credits\n50% used',
            'Currentsession\n16%used\nResets3pm(Europe/Madrid)\nCurrentweek(allmodels)\n27%used\nResetsOct13,8pm\nCurrentweek(Fable)\n2%used\nResetsOct13,8pm']:
            rows=parse_claude(text)
            self.assertEqual([r['available_percent'] for r in rows],[84,73,98])
            self.assertIn('3pm',rows[0]['reset_display'])

    def test_claude_unknown_format_not_a_quota(self):
        self.assertEqual(parse_claude('Usage: 0 input 0 output\nNo data'),[])

    def test_credits_keep_provider_units_and_do_not_invent_claude_balance(self):
        credits=codex_credits({'rateLimits':{'credits':{'hasCredits':True,'unlimited':False,'balance':'56298.0788555000'}}})
        self.assertEqual(credits[0]['balance'],'56298.0788555000')
        self.assertEqual(credits[0]['unit'],'provider credits')
        self.assertFalse(claude_credits('Usage credits\nUsage credits are off')['enabled'])
        self.assertIsNone(claude_credits('Usage credits\nUsage credits are off')['balance'])
        self.assertEqual(claude_credits('Usage credits\nBalance: €12.50')['balance'],'12.50')

    def test_terminal_cursor_redraw_and_split_escape(self):
        screen=Screen()
        screen.feed(b'\x1b[2J\x1b[1;1HCurrent session\r\n55% used\r\nResets 5pm')
        screen.feed(b'\x1b[2;1')
        screen.feed(b'H\x1b[2K20% used')
        self.assertEqual(parse_claude(screen.text())[0]['available_percent'],80)


if __name__=='__main__':unittest.main()

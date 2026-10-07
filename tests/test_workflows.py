import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.core import Manager, ManagerError, read_json
from ai_manager.handoff import handoff, handoff_prompt, safe_name
from ai_manager.cli import launch, limits, parser
from ai_manager.sessions import list_sessions, prepare_resume
from ai_manager.setup import setup, install_commands
from ai_manager.registry import add_account,rename_account,sync_identity
from ai_manager.ui import colored


class ProfileWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.home=Path(self.temp.name)
        for provider in ('codex','claude'):(self.home/('.'+provider)).mkdir()
        self.codex_credential=self.home/'.codex/auth.json'
        self.claude_credential=self.home/'.claude/.credentials.json'
        self.codex_credential.write_text(json.dumps({'tokens':{'access_token':'original-codex-secret'}}))
        self.claude_credential.write_text(json.dumps({'claudeAiOauth':{'accessToken':'original-claude-secret'}}))
        self.before=[p.read_bytes() for p in (self.codex_credential,self.claude_credential)]
        (self.home/'.codex/config.toml').write_text('model = "test"\n[mcp_servers.private.http_headers]\nAuthorization="Bearer '+('x'*40)+'"\n')
        (self.home/'.claude/settings.json').write_text(json.dumps({'model':'test','env':{'ANTHROPIC_API_KEY':'secret'},'hooks':{}}))
        (self.home/'.claude.json').write_text(json.dumps({'oauthAccount':{'secret':'must-stay-local'}}))
        instructions=self.home/'.codex/rules';instructions.mkdir()
        (instructions/'safe.md').write_text('Run the project tests.\n')
        (instructions/'unsafe.md').write_text('API_KEY='+('x'*40))
        claude_rules=self.home/'.claude/rules';claude_rules.mkdir()
        (claude_rules/'safe.md').write_text('Follow the project instructions.\n')
        self.m=Manager(self.home)
        with contextlib.redirect_stdout(io.StringIO()):setup(self.m)

    def tearDown(self):self.temp.cleanup()

    def test_five_profiles_and_no_credential_copies(self):
        self.assertEqual(len(self.m.accounts()),5)
        self.assertEqual([p.read_bytes() for p in (self.codex_credential,self.claude_credential)],self.before)
        for provider,number in [('codex','2'),('claude','2'),('claude','3')]:
            self.assertFalse(self.m.credential(self.m.account(provider,number)).exists())
            self.assertFalse(self.m.has_auth(self.m.account(provider,number)))
        self.assertFalse((self.home/'.claude-account-2/.claude.json').exists())
        self.assertTrue((self.home/'.codex-account-2/rules/safe.md').is_symlink())
        self.assertFalse((self.home/'.codex-account-2/rules/unsafe.md').exists())
        text=(self.home/'.codex-account-2/config.toml').read_text()
        self.assertNotIn('Authorization',text)
        self.assertIn('sqlite_home',text)
        self.assertNotIn('ANTHROPIC_API_KEY',(self.home/'.claude-account-2/settings.json').read_text())
        with contextlib.redirect_stdout(io.StringIO()):setup(self.m)

    def test_email_registration_renumber_and_inactive_accounts_preserve_auth(self):
        renamed=rename_account(self.m,'codex','1','4')
        self.assertEqual(renamed['home'],str(self.home/'.codex'))
        self.assertEqual(self.codex_credential.read_bytes(),self.before[0])
        second=add_account(self.m,'codex','chatgpt2@example.invalid','2')
        self.assertEqual(self.m.account('codex','chatgpt2@example.invalid'),second)
        self.assertFalse(self.m.has_auth(second))
        second['enabled']=False
        with self.assertRaises(ManagerError):self.m.account('codex','2')
        self.assertNotIn(second,self.m.accounts('codex'))
        self.assertEqual(self.m.account('codex','2',allow_inactive=True),second)

    def test_renumber_keeps_instruction_link_audit_addressable(self):
        rename_account(self.m,'claude','3','5')
        audit=read_json(self.m.state/'sharing-audit.json')['files']
        self.assertFalse(any(a['provider']=='claude' and a['account']=='3' for a in audit))
        self.assertTrue(any(a['provider']=='claude' and a['account']=='5' and a['shared'] for a in audit))
        for item in audit:
            account=self.m.account(item['provider'],item['account'],allow_inactive=True)
            if item['shared']:self.assertTrue((Path(account['home'])/item['relative_path']).exists())

    def test_login_email_mismatch_is_not_silently_assigned(self):
        account=self.m.account('codex','1');account['email']='expected@example.invalid'
        with patch('ai_manager.registry.identity',return_value='wrong@example.invalid'):
            with self.assertRaises(ManagerError):sync_identity(self.m,account)
        self.assertEqual(account['email'],'expected@example.invalid')

    def test_colors_in_tty_and_plain_pipes(self):
        with patch.dict(os.environ,{'AI_MANAGER_COLOR':'always'}):self.assertIn('\x1b[',colored('ready','ok'))
        with patch.dict(os.environ,{'AI_MANAGER_COLOR':'never'}):self.assertEqual(colored('ready','ok'),'ready')

    def test_launch_same_cwd_same_home_and_explicit_provider_account(self):
        args=argparse.Namespace(provider='codex',account='1',extra=[],dry_run=True)
        output=io.StringIO()
        with contextlib.redirect_stdout(output):self.assertEqual(launch(self.m,args),0)
        data=json.loads(output.getvalue())
        self.assertEqual(data['cwd'],str(Path.cwd()))
        self.assertEqual(data['home'],str(self.home/'.codex'))
        self.assertIn('--no-daemon',data['command'])
        self.assertTrue(any(v.startswith('sqlite_home=') for v in data['command']))

    def test_resume_uses_common_native_db_without_links_or_creator_rewrites(self):
        project=self.home/'project';project.mkdir()
        transcript=self.home/'.codex/sessions/2026/10/07/rollout-example.jsonl'
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({'type':'session_meta','payload':{'id':'thread-original','cwd':str(project)}})+'\n')
        db=self.home/'.codex/state_5.sqlite'
        c=sqlite3.connect(db)
        c.execute('create table threads (id text,rollout_path text,cwd text,updated_at integer,recency_at integer,source text,archived integer,has_user_event integer)')
        c.execute('insert into threads values (?,?,?,?,?,?,?,?)',('thread-original',str(transcript),str(project),999,500,'cli',0,1))
        c.commit();c.close()
        sessions=list_sessions(self.m,'codex',project)
        self.assertEqual(len(sessions),1)
        self.assertEqual(sessions[0]['updated'],500) # migration timestamps are not user recency
        original=transcript.read_bytes()
        self.assertEqual(prepare_resume(self.m,self.m.account('codex','2'),sessions[0]),['resume','thread-original'])
        self.assertEqual(transcript.read_bytes(),original)
        self.assertFalse((self.home/'.codex-account-2/sessions').exists())

    def test_claude_discovers_project_only_and_resumes_absolute_path(self):
        project=self.home/'project';project.mkdir()
        other=self.home/'other';other.mkdir()
        for name,cwd in [('first',project),('second',other)]:
            path=self.home/'.claude/projects/example'/f'{name}.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps({'type':'queue-operation'})+'\n'+json.dumps({'type':'user','sessionId':name,'cwd':str(cwd)})+'\n')
        sessions=list_sessions(self.m,'claude',project)
        self.assertEqual([s['id'] for s in sessions],['first'])
        command=prepare_resume(self.m,self.m.account('claude','2'),sessions[0])
        self.assertEqual(command,['--resume',sessions[0]['path']])
        self.assertFalse((self.home/'.claude-account-2/projects').exists())

    def test_failed_quota_query_does_not_present_previous_percentage_as_current(self):
        self.m.save_limits({'codex:1':{'provider':'codex','account':'1','status':'OK','queried_at':'2020-01-01T00:00:00+00:00',
                                       'windows':[{'name':'weekly','available_percent':50}],'available_percent':50}})
        args=argparse.Namespace(cached=False,refresh=True,json=True)
        with patch('ai_manager.automatic.query_limits',return_value={'provider':'codex','account':'1','status':'UNKNOWN','windows':[],
                                                             'available_percent':None,'reason':'timeout'}),contextlib.redirect_stdout(io.StringIO()):
            limits(self.m,args)
        row=self.m.cache()['accounts']['codex:1']
        self.assertIsNone(row['available_percent'])
        self.assertEqual(row['status'],'UNKNOWN')
        self.assertEqual(row['previous_success']['windows'][0]['available_percent'],50)

    def test_command_install_and_all_aliases_without_shell_changes(self):
        destination=self.home/'bin'
        with contextlib.redirect_stdout(io.StringIO()):install_commands(Path(__file__).resolve().parent.parent,destination)
        self.assertEqual(len(list(destination.iterdir())),21)
        for name in ('ai','x1','x2','c1','c2','c3','x1r','x2r','c1r','c2r','c3r','claude2','claude2r','codex2','codex2r'):
            self.assertTrue(os.access(destination/name,os.X_OK))
        self.assertTrue(parser().parse_args(['update','--check']).check)


class HandoffTests(unittest.TestCase):
    def test_git_summary_omits_secret_files_and_content_and_preserves_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);repo=home/'repo';repo.mkdir()
            def git(*args):return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True)
            git('init','-b','test')
            git('config','user.name','Test');git('config','user.email','test@example.invalid')
            (repo/'app.py').write_text('print("before")\n');git('add','app.py');git('commit','-m','initial')
            (repo/'app.py').write_text('secret = "'+('sk-ant-'+'x'*40)+'"\n')
            (repo/'.env').write_text('PASSWORD=never-in-handoff')
            (repo/'secrets').mkdir();(repo/'secrets/creds.txt').write_text('private')
            manager=Manager(home);manager.state.mkdir(parents=True)
            with contextlib.redirect_stdout(io.StringIO()):
                path=handoff(manager,repo,{'task':'Implement safe code','tests':'python tests.py (passed)','decision':'password='+('x'*30)})
            text=path.read_text()
            self.assertIn('app.py',text)
            for secret in ('.env','never-in-handoff','sk-ant-','creds.txt','x'*30):self.assertNotIn(secret,text)
            self.assertIn('Tests failing',text);self.assertIn('Not recorded',text)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertIn('.ai/',(repo/'.gitignore').read_text())
            self.assertTrue(git('check-ignore','.ai/handoff.md').returncode==0)
            with contextlib.redirect_stdout(io.StringIO()):handoff(manager,repo,{})
            self.assertIn('Implement safe code',path.read_text())
            self.assertIn(str(path),handoff_prompt(manager,repo))

    def test_handoff_refuses_external_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);repo=home/'repo';repo.mkdir();other=home/'outside';other.mkdir()
            (repo/'.ai').symlink_to(other,target_is_directory=True)
            m=Manager(home)
            with self.assertRaises(ManagerError):handoff(m,repo,{})
            self.assertFalse((other/'handoff.md').exists())


if __name__=='__main__':unittest.main()

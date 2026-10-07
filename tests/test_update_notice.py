import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.core import Manager, ManagerError, write_json
from ai_manager.distribution import install
from ai_manager.update_notice import CACHE_SECONDS, REPLAY, maybe_update


class TTYOutput(io.StringIO):
    def isatty(self):return True


class UpdateNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.manager=Manager(self.root/'home');self.manager.state.mkdir(parents=True)
        self.base=self.root/'prefix/lib/ai-command';self.base.mkdir(parents=True)
        write_json(self.base/'install.json',{'prefix':str(self.root/'prefix'),'version':'v1.0.0'})
        self.args=argparse.Namespace(command='resume')
        self.argv=['resume','agy','--session','session-id','--','--prompt','text with spaces']
        self.info={'tag_name':'v1.0.1','assets':[{'name':'ai-command-v1.0.1.tar.gz'},{'name':'SHA256SUMS'}]}
        self.tty_in=patch('ai_manager.update_notice.sys.stdin.isatty',return_value=True);self.tty_in.start()
        self.tty_out=patch('ai_manager.update_notice.sys.stdout.isatty',return_value=True);self.tty_out.start()
        self.installation=patch('ai_manager.update_notice.installation',return_value=self.base);self.installation.start()
        self.env=patch.dict(os.environ,{REPLAY:''});self.env.start()

    def tearDown(self):
        self.env.stop();self.installation.stop();self.tty_out.stop();self.tty_in.stop();self.temp.cleanup()

    def test_accept_updates_exact_tag_then_reexecutes_original_arguments_and_environment(self):
        cwd=Path.cwd()
        with patch('ai_manager.update_notice.release_info',return_value=self.info) as check, \
             patch('builtins.input',return_value='s'),patch('ai_manager.update_notice.update') as update, \
             patch('ai_manager.update_notice.os.execve',side_effect=SystemExit(0)) as execute, \
             contextlib.redirect_stdout(TTYOutput()):
            with self.assertRaises(SystemExit):maybe_update(self.manager,self.argv,self.args)
        check.assert_called_once_with(timeout=2)
        self.assertEqual(update.call_args.args[0].to,'v1.0.1')
        path,arguments,env=execute.call_args.args
        self.assertEqual(arguments,[path,*self.argv]);self.assertEqual(env[REPLAY],'1')
        self.assertEqual(env.get('HOME'),os.environ.get('HOME'));self.assertEqual(Path.cwd(),cwd)

    def test_decline_keeps_command_and_caches_check_and_offer(self):
        with patch('ai_manager.update_notice.release_info',return_value=self.info) as check, \
             patch('builtins.input',return_value='n') as question,patch('ai_manager.update_notice.update') as update, \
             contextlib.redirect_stdout(TTYOutput()):
            maybe_update(self.manager,self.argv,self.args);maybe_update(self.manager,self.argv,self.args)
        self.assertEqual(check.call_count,1);self.assertEqual(question.call_count,1);update.assert_not_called()

    def test_offline_failure_does_not_block_and_is_cached(self):
        with patch('ai_manager.update_notice.release_info',side_effect=ManagerError('offline')) as check, \
             patch('builtins.input') as question:
            maybe_update(self.manager,self.argv,self.args);maybe_update(self.manager,self.argv,self.args)
        self.assertEqual(check.call_count,1);question.assert_not_called()

    def test_noninteractive_structured_and_explicit_updates_never_prompt(self):
        with patch('ai_manager.update_notice.release_info') as check:
            for flag in ('--json','--dry-run','--cached'):
                maybe_update(self.manager,[*self.argv,flag],self.args)
            maybe_update(self.manager,['update'],argparse.Namespace(command='update'))
            with patch('ai_manager.update_notice.sys.stdin.isatty',return_value=False):
                maybe_update(self.manager,self.argv,self.args)
        check.assert_not_called()

    def test_replay_is_one_shot_and_incomplete_release_does_not_offer(self):
        with patch.dict(os.environ,{REPLAY:'1'}),patch('ai_manager.update_notice.release_info') as check:
            maybe_update(self.manager,self.argv,self.args);check.assert_not_called();self.assertNotIn(REPLAY,os.environ)
        with patch('ai_manager.update_notice.release_info',return_value={'tag_name':'v1.0.1','assets':[]}), \
             patch('builtins.input') as question:
            maybe_update(self.manager,self.argv,self.args)
        question.assert_not_called()

    def test_update_failure_continues_original_command_without_reexec(self):
        with patch('ai_manager.update_notice.release_info',return_value=self.info), \
             patch('builtins.input',return_value='s'),patch('ai_manager.update_notice.update',side_effect=ManagerError('failed')), \
             patch('ai_manager.update_notice.os.execve') as execute,contextlib.redirect_stdout(TTYOutput()), \
             contextlib.redirect_stderr(io.StringIO()):
            maybe_update(self.manager,self.argv,self.args)
        execute.assert_not_called()

    def test_real_install_and_reexec_runs_original_command_in_original_directory(self):
        repo=Path(__file__).resolve().parent.parent
        old=self.root/'old';new=self.root/'new'
        for folder,version in ((old,'1.0.0'),(new,'1.0.1')):
            folder.mkdir()
            shutil.copytree(repo/'ai_manager',folder/'ai_manager',ignore=shutil.ignore_patterns('__pycache__'))
            (folder/'VERSION').write_text(version+'\n')
            (folder/'ai_manager/__init__.py').write_text('__version__ = '+repr(version)+'\n')
        home=self.root/'replay-home';home.mkdir()
        config=home/'.config/ai-manager/config.json';config.parent.mkdir(parents=True)
        write_json(config,{'schema':1,'manager_schema':1,'accounts':[],'custom':'preserved'})
        auth=home/'.codex/auth.json';auth.parent.mkdir();auth.write_text('credential-test-fixture')
        before=(config.read_bytes(),auth.read_bytes())
        prefix=self.root/'replay-prefix'
        with patch.dict(os.environ,{'HOME':str(home)}):install(old,prefix,auto=False)
        project=self.root/'project';project.mkdir()
        subprocess.run(['git','init','-q',str(project)],check=True)
        subprocess.run(['git','-C',str(project),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                        'commit','--allow-empty','-qm','Fixture repository'],check=True)
        task='Continue the exact task with spaces and symbols: [] = --example'
        code='''
import sys,builtins
sys.path.insert(0,sys.argv[1])
from ai_manager import update_notice
from ai_manager.distribution import install
from ai_manager.cli import main
update_notice.sys.stdin.isatty=lambda:True
update_notice.sys.stdout.isatty=lambda:True
builtins.input=lambda question:'s'
update_notice.release_info=lambda **kw:{'tag_name':'v1.0.1','assets':[{'name':'ai-command-v1.0.1.tar.gz'},{'name':'SHA256SUMS'}]}
update_notice.update=lambda args:install(sys.argv[2],sys.argv[3],auto=False)
raise SystemExit(main(['handoff','--task',sys.argv[4]]))
'''
        env=dict(os.environ,HOME=str(home));env.pop(REPLAY,None)
        result=subprocess.run([sys.executable,'-c',code,str(prefix/'lib/ai-command/current'),str(new),str(prefix),task],
                              cwd=project,env=env,capture_output=True,text=True,timeout=25)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn(task,(project/'.ai/handoff.md').read_text())
        self.assertEqual(json.loads((prefix/'lib/ai-command/install.json').read_text())['version'],'v1.0.1')
        self.assertEqual((config.read_bytes(),auth.read_bytes()),before)

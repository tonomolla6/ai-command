import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.cli import main
from ai_manager.core import Manager, read_json, write_json
from ai_manager.entrypoints import single_provider_main
from ai_manager.permissions import single_tool_arguments
from ai_manager.setup import install_native_shell
from ai_manager.single_tools import launch_tool


class SinglePermissionsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'HOME':str(self.home)},clear=False);self.env.start()
        self.manager=Manager(self.home);self.manager.state.mkdir(parents=True)
        self.manager.config={'schema':1,'manager_schema':1,'accounts':[],
                             'agy_skip_permissions':True,'opencode_skip_permissions':True}

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def test_new_and_resumed_managed_launches_receive_official_flags(self):
        for provider,flag in [('agy','--dangerously-skip-permissions'),('opencode','--auto')]:
            for resume in (False,True):
                out=io.StringIO()
                args=argparse.Namespace(provider=provider,model=None,extra=[],dry_run=True,pick=False)
                with patch('ai_manager.single_tools.executable',return_value='/official/'+provider), \
                     patch('ai_manager.single_tools.models',return_value=[{'id':'opencode/free'}]), \
                     patch('ai_manager.single_tools.list_tool_sessions',return_value=[{'id':'session-current'}]), \
                     patch('ai_manager.single_tools.handoff_prompt',return_value=None),contextlib.redirect_stdout(out):
                    launch_tool(self.manager,args,resume)
                command=json.loads(out.getvalue())['command']
                self.assertEqual(command[1],flag)
                if resume:self.assertIn('session-current',command)

    def test_native_new_resume_and_run_use_policy_and_preserve_args(self):
        for provider,argv,flag in [('agy',[],'--dangerously-skip-permissions'),
                ('agy',['--conversation','conversation-current'],'--dangerously-skip-permissions'),
                ('opencode',['--session','session-current'],'--auto'),
                ('opencode',['run','--continue','example task'],'--auto')]:
            with patch('ai_manager.entrypoints.Manager',return_value=self.manager), \
                 patch('ai_manager.entrypoints.executable',return_value='/official/'+provider), \
                 patch('ai_manager.entrypoints.os.execve',side_effect=SystemExit(0)) as execute:
                with self.assertRaises(SystemExit):single_provider_main(provider,argv)
            path,command,env=execute.call_args.args
            self.assertEqual(command,[path,flag,*argv])
            if provider=='agy':self.assertEqual(env['AGY_CLI_DISABLE_AUTO_UPDATE'],'1')

    def test_admin_help_and_disabled_policy_pass_through(self):
        for provider in ('agy','opencode'):
            for argv in (['--version'],['--help'],['models'],['--model','demo','models']):
                self.assertEqual(single_tool_arguments(self.manager,provider,argv),argv)
            self.manager.config[provider+'_skip_permissions']=False
            self.assertEqual(single_tool_arguments(self.manager,provider,['--continue']),['--continue'])
            self.manager.config.pop(provider+'_skip_permissions')
            self.assertEqual(single_tool_arguments(self.manager,provider,[]),[])

    def test_overrides_are_normalized_without_editing_prompt_values(self):
        self.assertEqual(single_tool_arguments(self.manager,'opencode',['--no-auto','--auto=false','--session','demo']),
                         ['--auto','--session','demo'])
        self.assertEqual(single_tool_arguments(self.manager,'agy',['--dangerously-skip-permissions=false','--prompt','--help']),
                         ['--dangerously-skip-permissions','--prompt','--help'])
        self.assertEqual(single_tool_arguments(self.manager,'opencode',['--','--auto=false']),['--auto','--','--auto=false'])
        self.assertEqual(single_tool_arguments(self.manager,'agy',['--sandbox','--sandbox=true','--conversation','demo']),
                         ['--dangerously-skip-permissions','--conversation','demo'])
        self.assertEqual(single_tool_arguments(self.manager,'agy',['--prompt','--sandbox']),
                         ['--dangerously-skip-permissions','--prompt','--sandbox'])

    def test_configure_backs_up_and_only_changes_selected_policies(self):
        self.manager.config_dir.mkdir(parents=True)
        original={'schema':1,'manager_schema':1,'accounts':[],'custom':'keep','claude_skip_permissions':False}
        write_json(self.manager.config_path,original)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['configure','--agy-danger','on','--opencode-danger','on']),0)
        config=read_json(self.manager.config_path)
        self.assertTrue(config['agy_skip_permissions']);self.assertTrue(config['opencode_skip_permissions'])
        self.assertFalse(config['claude_skip_permissions']);self.assertEqual(config['custom'],'keep')
        backups=list((self.home/'.ai-manager/backups').glob('*/.config/ai-manager/config.json'))
        self.assertEqual(len(backups),1);self.assertEqual(read_json(backups[0]),original)

    def test_shell_integration_is_idempotent_and_forwards_exact_native_arguments(self):
        bin_dir=self.home/'bin';bin_dir.mkdir();fake=bin_dir/'ai'
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n');fake.chmod(0o755)
        self.manager.config['command_bin']=str(bin_dir)
        bashrc=self.home/'.bashrc';original='export KEEP_ME=yes\nalias agy="false"\n';bashrc.write_text(original)
        with contextlib.redirect_stdout(io.StringIO()):
            install_native_shell(self.manager);first=bashrc.read_text();install_native_shell(self.manager)
        self.assertEqual(first,bashrc.read_text());self.assertTrue(first.startswith(original))
        result=subprocess.run(['bash','--noprofile','--norc','-c','. "$1"; agy --conversation demo; opencode --session demo','_',str(bashrc)],capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.splitlines(),['native','agy','--','--conversation','demo',
                                                   'native','opencode','--','--session','demo'])
        backups=list((self.home/'.ai-manager/backups').glob('*/.bashrc'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_text(),original)

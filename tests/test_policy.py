import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.cli import launch
from ai_manager.core import Manager, ManagerError, write_json
from ai_manager.registry import fixed_model_arguments, set_codex_policy


class ReservedModelTests(unittest.TestCase):
    def test_reserved_model_applies_to_launch_and_resume_and_rejects_overrides(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'HOME':'/tmp/test','PATH':os.defpath},clear=True):
            m=Manager(tmp);m.state.mkdir(parents=True)
            a={'provider':'codex','account':'1','home':tmp,'label':'Codex 1','fixed_model':'gpt-6-luna',
               'automatic':False,'email':'admin@example.invalid','shared_sqlite_home':tmp}
            m.config={'schema':1,'accounts':[a]}
            args=argparse.Namespace(provider='codex',account='1',dry_run=True,extra=[])
            for session in (None,{'id':'thread-id'}):
                out=io.StringIO()
                with patch('ai_manager.cli.handoff_prompt',return_value=None), \
                     patch('ai_manager.cli.executable',return_value='/official/codex'), \
                     patch('ai_manager.cli.prepare_resume',return_value=['resume','thread-id']), \
                     contextlib.redirect_stdout(out):
                    launch(m,args,resume=session is not None,session=session)
                command=json.loads(out.getvalue())['command']
                self.assertIn('gpt-6-luna',command)
                self.assertEqual(command[command.index('--model')+1],'gpt-6-luna')
            for extras in (['--model','other'],['-mother'],['--model=other'],['--profile','other'],
                           ['-c','model="other"'],['--config=model="other"'],['-cmodel="other"']):
                with self.subTest(extras=extras),self.assertRaises(ManagerError):fixed_model_arguments(a,extras)
            self.assertEqual(fixed_model_arguments(a,['-c','model_reasoning_effort="high"']),['--model','gpt-6-luna'])

    def test_policy_requires_official_model_and_never_changes_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Manager(tmp);m.state.mkdir(parents=True);m.config_dir.mkdir(parents=True)
            a={'provider':'codex','account':'1','home':tmp,'label':'Codex 1','email':'admin@example.invalid'}
            m.config={'schema':1,'accounts':[a]};write_json(m.config_path,m.config)
            credential=Path(tmp)/'auth.json';write_json(credential,{'tokens':{'access_token':'fake-test'}})
            before=credential.read_bytes()
            with patch('ai_manager.registry.sync_identity',return_value=a['email']), \
                 patch('ai_manager.registry.CodexRPC') as rpc:
                rpc.return_value.__enter__.return_value.call.return_value={'data':[{'model':'gpt-6-luna'}]}
                with self.assertRaises(ManagerError):set_codex_policy(m,a,'unavailable-model',False,'Free')
                self.assertNotIn('fixed_model',a)
                set_codex_policy(m,a,'gpt-6-luna',False,'Free')
            self.assertEqual(a['fixed_model'],'gpt-6-luna')
            self.assertFalse(a['automatic'])
            self.assertEqual(credential.read_bytes(),before)

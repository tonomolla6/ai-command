import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.core import Manager, ManagerError, write_json
from ai_manager.single_tools import (group_agy_models, launch_tool, list_tool_sessions,
                                     parse_agy_credits, parse_agy_usage, parse_opencode_models, query_usage)


class SingleAccountToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'HOME':str(self.home),'PATH':os.defpath},clear=True);self.env.start()
        self.m=Manager(self.home);self.m.state.mkdir(parents=True)

    def tearDown(self):self.env.stop();self.temp.cleanup()

    def test_agy_keeps_both_buckets_and_windows_and_zero_credits(self):
        text='\n'.join(f'{group}\t{window} Limit Remaining\t{percent}%\t2026-10-14T09:00:00Z'
                       for group in ('Gemini Models','Claude and GPT models')
                       for window,percent in [('Weekly',55),('Five Hour',100)])
        rows=parse_agy_usage(text)
        self.assertEqual(len(rows),4)
        self.assertEqual([r['available_percent'] for r in rows],[55,100,55,100])
        self.assertEqual([r['window_minutes'] for r in rows],[10080,300,10080,300])
        credits=parse_agy_credits('Remaining credits\t0\nUpgrade\thttps://example.invalid/upgrade')
        self.assertEqual(credits['display'],'0 créditos')
        self.assertNotIn('upgrade',json.dumps(credits).lower())
        self.assertIsNone(parse_agy_credits('Remaining credits\tUNKNOWN'))

    def test_agy_catalog_groups_only_real_effort_variants(self):
        catalog=[{'id':'flash-'+effort,'name':'Gemini 3.8 Flash ('+effort.title()+')'}
                 for effort in ('high','medium','low')]
        catalog+=[{'id':'gpt-medium','name':'GPT-OSS 120B (Medium)'}]
        grouped=group_agy_models(catalog)
        self.assertEqual([m['name'] for m in grouped],['Gemini 3.8 Flash','GPT-OSS 120B'])
        self.assertEqual(grouped[0]['efforts'],['low','medium','high'])
        self.assertEqual(grouped[1]['efforts'],['medium'])
        self.assertEqual(len(grouped[0]['ids']),3)

    def test_open_code_free_catalog_rejects_paid_inactive_and_non_tool_models(self):
        base={'name':'Free Test','status':'active','capabilities':{'toolcall':True},
              'cost':{'input':0,'output':0,'cache':{'read':0,'write':0}},'release_date':'2026-10-01',
              'limit':{'context':100000},'headers':{'Authorization':'fake-private-header'}}
        catalog={'good':base,'paid':dict(base,cost={'input':0,'output':1}),
                 'cached-paid':dict(base,cost={'input':0,'output':0,'cache':{'read':1}}),
                 'missing-cost':dict(base,cost={}), 'inactive':dict(base,status='deprecated'),
                 'non-tool':dict(base,capabilities={'toolcall':False})}
        text='\n'.join('opencode/'+key+'\n'+json.dumps(data) for key,data in catalog.items())
        rows=parse_opencode_models(text)
        self.assertEqual([r['id'] for r in rows],['opencode/good'])
        self.assertNotIn('Authorization',json.dumps(rows))
        self.assertNotIn('fake-private-header',json.dumps(rows))

    def test_usage_uses_only_official_local_read_commands(self):
        account={'provider':'agy','account':'current','single':True}
        outputs={('-p','/usage'):'Gemini Models\tWeekly Limit Remaining\t90%\t2026-10-14T09:00:00Z',
                 ('-p','/credits'):'Remaining credits\t0'}
        with patch('ai_manager.single_tools.run_read',side_effect=lambda provider,args,cwd:outputs[tuple(args)]) as read, \
             patch('ai_manager.single_tools.models',return_value=[{'id':'flash-low','name':'Gemini Flash (Low)'}]):
            row=query_usage(self.m,account)
        self.assertEqual(row['status'],'OK');self.assertEqual(row['credits']['balance'],'0')
        self.assertEqual(row['models'][0]['name'],'Gemini Flash')
        self.assertEqual([call.args[1] for call in read.call_args_list],[['-p','/usage'],['-p','/credits']])

    def test_free_launch_preserves_credentials_and_selects_latest_exact_cwd_session(self):
        auth=self.home/'.local/share/opencode/auth.json';auth.parent.mkdir(parents=True)
        write_json(auth,{'provider':{'type':'oauth','access':'fake-only-current-login'}});before=auth.read_bytes()
        data=[{'id':'older','directory':str(Path.cwd()),'updated':10},
              {'id':'newest','directory':str(Path.cwd()),'updated':20},
              {'id':'other-project','directory':str(self.home),'updated':999}]
        with patch('ai_manager.single_tools.run_read',return_value=json.dumps(data)):
            self.assertEqual([s['id'] for s in list_tool_sessions(self.m,'opencode',Path.cwd())],['newest','older'])
            args=argparse.Namespace(provider='opencode',model=None,extra=[],dry_run=True,pick=False)
            out=io.StringIO()
            with patch('ai_manager.single_tools.models',return_value=[{'id':'opencode/free'}]), \
                 patch('ai_manager.single_tools.executable',return_value='/official/opencode'), \
                 patch('ai_manager.single_tools.handoff_prompt',return_value=None),contextlib.redirect_stdout(out):
                launch_tool(self.m,args,resume=True)
                command=json.loads(out.getvalue())['command']
                self.assertEqual(command,['/official/opencode','--model','opencode/free','--session','newest'])
                args.model='opencode/paid'
                with self.assertRaises(ManagerError):launch_tool(self.m,args)
                args.model=None;args.extra=['--','--model=opencode/paid']
                with self.assertRaises(ManagerError):launch_tool(self.m,args)
        self.assertEqual(auth.read_bytes(),before)

    def test_agy_resume_reads_only_workspace_mapping_without_changing_auth(self):
        cache=self.home/'.gemini/antigravity-cli/cache';cache.mkdir(parents=True)
        write_json(cache/'last_conversations.json',{str(Path.cwd()):'conversation-current',str(self.home):'other'})
        rows=list_tool_sessions(self.m,'agy',Path.cwd())
        self.assertEqual([r['id'] for r in rows],['conversation-current'])

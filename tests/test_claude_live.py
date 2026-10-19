"""Official statusline observations must stay isolated, bounded and dated."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
import shlex
import contextlib
import io
from unittest.mock import patch

from ai_manager.core import Manager, read_json, write_json
from ai_manager.claude_live import configure, observe, merge_entry, _instance


class LiveUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.m=Manager(self.home)
        self.m.state.mkdir(parents=True);self.m.config_dir.mkdir(parents=True)
        self.account={'provider':'claude','account':'2','home':str(self.home/'profile'),
                      'email':'two@example.invalid','email_verified':True,'label':'Claude 2'}
        profile=Path(self.account['home']);profile.mkdir()
        write_json(profile/'.credentials.json',{'claudeAiOauth':{'accessToken':'fixture-only'}})
        write_json(profile/'.claude.json',{'oauthAccount':{'emailAddress':self.account['email']}})
        self.m.config={'schema':1,'accounts':[self.account]};write_json(self.m.config_path,self.m.config)
        self.at=dt.datetime.now(dt.timezone.utc)
        self.payload={'session_id':'example-session','cost':{'total_api_duration_ms':0},'prompt_cache':{'requests':0}}

    def response(self,api_ms=1000,used=96):
        payload=copy.deepcopy(self.payload);payload['cost']['total_api_duration_ms']=api_ms
        payload['prompt_cache']['requests']=api_ms//1000
        payload['rate_limits']={
            'five_hour':{'used_percentage':used,'resets_at':(self.at+dt.timedelta(hours=2)).timestamp()},
            'seven_day':{'used_percentage':13,'resets_at':(self.at+dt.timedelta(days=3)).timestamp()}}
        payload['sensitive_extra']='fixture-private-text-must-not-be-saved'
        return payload

    def baseline(self):
        self.assertFalse(observe(self.m,self.account,self.payload,'one-process',self.at))

    def test_normal_reply_publishes_quota_without_a_provider_call(self):
        self.baseline()
        with patch('subprocess.run',side_effect=AssertionError('unexpected provider query')):
            self.assertTrue(observe(self.m,self.account,self.response(),'one-process',self.at))
        row=self.m.cache()['accounts']['claude:2']
        self.assertEqual([w['available_percent'] for w in row['windows']],[4,87])
        self.assertTrue(row['email_verified']);self.assertEqual(row['source_type'],'claude_statusline')
        self.assertEqual(row['queried_at'],self.at.isoformat())
        self.assertNotIn('fixture-private-text', ''.join(p.read_text() for p in self.m.state.glob('*.json')))

    def test_redraw_and_idle_timer_do_not_renew_observation_age(self):
        self.baseline();observe(self.m,self.account,self.response(),'one-process',self.at)
        later=self.at+dt.timedelta(seconds=119)
        self.assertFalse(observe(self.m,self.account,self.response(),'one-process',later))
        row=merge_entry(self.m,self.account,{},later)
        self.assertEqual(row['queried_at'],self.at.isoformat())
        self.assertEqual(merge_entry(self.m,self.account,{},self.at+dt.timedelta(seconds=120)),{})

    def test_inflight_time_or_redraw_with_same_request_counter_is_not_a_new_reading(self):
        self.baseline();observe(self.m,self.account,self.response(),'one-process',self.at)
        payload=self.response();payload['cost']['total_api_duration_ms']=9000
        self.assertFalse(observe(self.m,self.account,payload,'one-process',self.at+dt.timedelta(seconds=119)))
        self.assertEqual(merge_entry(self.m,self.account,{},self.at+dt.timedelta(seconds=120)),{})

    def test_old_session_first_render_and_process_restart_need_a_new_response(self):
        self.assertFalse(observe(self.m,self.account,self.response(),'old-process',self.at))
        self.assertNotIn('claude:2',self.m.cache()['accounts'])
        self.assertTrue(observe(self.m,self.account,self.response(2000),'old-process',self.at))
        self.assertFalse(observe(self.m,self.account,self.response(3000),'new-process',self.at))

    def test_logout_changed_identity_and_other_accounts_cannot_reuse_observation(self):
        self.baseline();observe(self.m,self.account,self.response(),'one-process',self.at)
        for change in ({'email':'different@example.invalid'},{'account':'9'},{'enabled':False}):
            self.assertEqual(merge_entry(self.m,dict(self.account,**change),{},self.at),{})
        self.assertEqual(merge_entry(self.m,self.account,{'status':'SIN LOGIN'},self.at),{'status':'SIN LOGIN'})
        rejected={'status':'UNKNOWN','email_verified':False,'reason':'Identity mismatch'}
        self.assertEqual(merge_entry(self.m,self.account,rejected,self.at),rejected)
        Path(self.account['home'],'.credentials.json').unlink()
        self.assertEqual(merge_entry(self.m,self.account,{},self.at),{})

    def test_missing_or_invalid_windows_are_not_invented(self):
        for invalid in (True,float('nan'),-1,101,'96'):
            with self.subTest(invalid=invalid):
                self.baseline();payload=self.response();payload['rate_limits']['seven_day']['used_percentage']=invalid
                self.assertFalse(observe(self.m,self.account,payload,'one-process',self.at))
        self.baseline();payload=self.response();payload['rate_limits'].pop('seven_day')
        self.assertFalse(observe(self.m,self.account,payload,'one-process',self.at))

    def test_expired_reset_and_future_observation_are_not_current(self):
        self.baseline();observe(self.m,self.account,self.response(),'one-process',self.at)
        self.assertEqual(merge_entry(self.m,self.account,{},self.at-dt.timedelta(seconds=1)),{})
        state=read_json(self.m.state/'claude-live.json')
        state['accounts']['claude:2']['windows'][0]['reset_at']=(self.at-dt.timedelta(seconds=1)).isoformat()
        write_json(self.m.state/'claude-live.json',state)
        self.assertEqual(merge_entry(self.m,self.account,{},self.at),{})

    def test_failed_probe_uses_fresh_observation_with_its_original_timestamp(self):
        self.baseline();observe(self.m,self.account,self.response(),'one-process',self.at)
        attempted=self.at+dt.timedelta(seconds=40)
        row=merge_entry(self.m,self.account,{'status':'UNKNOWN','reason':'stale provider fallback',
                       'queried_at':attempted.isoformat()},attempted)
        self.assertEqual(row['status'],'OK');self.assertEqual(row['queried_at'],self.at.isoformat())
        self.assertEqual(row['last_attempt_at'],attempted.isoformat())

    def test_fresh_provider_response_keeps_extra_windows_and_credits(self):
        self.baseline();observe(self.m,self.account,self.response(),'one-process',self.at)
        row={'status':'OK','queried_at':(self.at+dt.timedelta(seconds=1)).isoformat(),
             'windows':[{'name':'Fable','available_percent':0}],'credits':{'enabled':False}}
        self.assertEqual(merge_entry(self.m,self.account,row,self.at+dt.timedelta(seconds=1)),row)

    def test_new_responses_do_not_rejuvenate_older_scoped_windows_or_credits(self):
        self.baseline();observe(self.m,self.account,self.response(),'one-process',self.at)
        earlier=self.at-dt.timedelta(seconds=110)
        row={'status':'OK','queried_at':earlier.isoformat(),
             'windows':[{'name':'Fable','available_percent':50}], 'credits':{'enabled':False}}
        merged=merge_entry(self.m,self.account,row,self.at)
        self.assertEqual(len(merged['windows']),3)
        self.assertEqual(merged['credits_observed_at'],earlier.isoformat())
        later=self.at+dt.timedelta(seconds=20)
        idle=merge_entry(self.m,self.account,merged,later)
        self.assertEqual(len(idle['windows']),2)
        self.assertIsNone(idle['credits'])
        observe(self.m,self.account,self.response(2000),'one-process',later)
        result=merge_entry(self.m,self.account,merged,later)
        self.assertEqual(len(result['windows']),2)
        self.assertIsNone(result['credits'])

    def test_configuration_preserves_settings_credentials_and_original_statusline_with_backup(self):
        profile=Path(self.account['home']);settings=profile/'settings.json'
        original={'permissions':{'defaultMode':'auto'},'statusLine':{'type':'command','command':'printf existing','padding':2}}
        write_json(settings,original);credential=(profile/'.credentials.json').read_bytes()
        configure(self.m,True)
        current=read_json(settings)
        self.assertEqual(current['permissions'],original['permissions'])
        self.assertEqual(current['statusLine']['padding'],2)
        self.assertNotEqual(current['statusLine']['command'],original['statusLine']['command'])
        self.assertEqual((profile/'.credentials.json').read_bytes(),credential)
        self.assertTrue(list((self.home/'.ai-manager/backups').glob('*/profile/settings.json')))
        before=settings.read_bytes();configure(self.m,True);self.assertEqual(settings.read_bytes(),before)
        configure(self.m,False);self.assertEqual(read_json(settings),original)

    def test_real_helper_preserves_custom_output_and_stdin_without_logging_payload(self):
        settings=Path(self.account['home'])/'settings.json'
        original=shlex.join([sys.executable,'-c',
            'import json,sys;d=json.load(sys.stdin);print("ORIGINAL:"+str(d["cost"]["total_api_duration_ms"]))'])
        write_json(settings,{'statusLine':{'type':'command','command':original}})
        configure(self.m,True)
        command=shlex.split(read_json(settings)['statusLine']['command'])
        env={**os.environ,'HOME':str(self.home),'CLAUDE_CONFIG_DIR':self.account['home'],
             'AI_MANAGER_CLAUDE_RUN':'fixture-native-process'}
        for payload in (self.payload,self.response()):
            result=subprocess.run(command,input=json.dumps(payload),text=True,capture_output=True,env=env,timeout=5)
            self.assertEqual(result.returncode,0);self.assertEqual(result.stderr,'')
            self.assertEqual(result.stdout,'ORIGINAL:'+str(payload['cost']['total_api_duration_ms'])+'\n')
        self.assertEqual(self.m.cache()['accounts']['claude:2']['available_percent'],4)
        self.assertEqual((self.m.state/'claude-live.json').stat().st_mode&0o777,0o600)

    def test_reconfiguration_after_interpreter_change_does_not_chain_the_helper_to_itself(self):
        configure(self.m,True)
        with patch('ai_manager.claude_live.sys.executable','/new/python'):
            configure(self.m,True)
        configure(self.m,False)
        self.assertNotIn('statusLine',read_json(Path(self.account['home'])/'settings.json'))

    def test_concurrent_native_probe_failure_uses_live_quota_after_official_identity_check(self):
        from ai_manager.providers import query_limits
        from ai_manager.core import ManagerError
        self.baseline();observe(self.m,self.account,self.response(),'one-process',self.at)
        with patch('ai_manager.providers.executable',return_value='/fixture/claude'), \
             patch('ai_manager.providers.subprocess.run') as run, \
             patch('ai_manager.claude_setup.repair_onboarding'), \
             patch('ai_manager.providers.claude_usage',side_effect=ManagerError('Provider returned stale usage')):
            run.return_value.returncode=0
            run.return_value.stdout=json.dumps({'loggedIn':True,'email':self.account['email']})
            result=query_limits(self.m,self.account)
        self.assertEqual(result['status'],'OK');self.assertEqual(result['available_percent'],4)
        self.assertEqual(result['queried_at'],self.at.isoformat())

    def test_auto_refresh_does_not_make_an_old_statusline_observation_young_again(self):
        from ai_manager.automatic import refresh_accounts
        self.baseline();observe(self.m,self.account,self.response(),'one-process',self.at)
        row=self.m.cache()['accounts']['claude:2']
        with patch('ai_manager.automatic.collect_limits',return_value={'claude:2':row}):
            result=refresh_accounts(self.m,'claude',force=True)
        self.assertEqual(result['claude:2']['queried_at'],self.at.isoformat())

    def test_configure_command_does_not_change_permission_policy(self):
        from ai_manager.cli import main
        from ai_manager.migrations import CURRENT
        self.m.config.update(manager_schema=CURRENT,claude_skip_permissions=True)
        write_json(self.m.config_path,self.m.config);before=self.m.config_path.read_bytes()
        with patch.dict(os.environ,{'HOME':str(self.home)}),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['configure','--claude-live-usage','on']),0)
        self.assertTrue(read_json(self.m.state/'claude-live-settings.json')['enabled'])
        self.assertEqual(self.m.config_path.read_bytes(),before)

    def test_native_ancestor_detection_supports_npm_exe_deleted_inodes_and_official_version_paths(self):
        proc=self.home/'proc';proc.mkdir()
        for pid,parent,exe,start in [(101,202,'/bin/sh','111'),(202,1,'/package/claude.exe (deleted)','222')]:
            path=proc/str(pid);path.mkdir()
            (path/'stat').write_text(str(pid)+' (fixture) '+' '.join(['S',str(parent),*['0']*17,start]))
            (path/'comm').write_text('claude\n');(path/'exe').symlink_to(exe)
        with patch.dict(os.environ,{},clear=True):
            for name in ('claude.exe (deleted)','claude','2.1.303'):
                (proc/'202/exe').unlink();(proc/'202/exe').symlink_to('/native/'+name)
                self.assertEqual(_instance(proc,101),'202:222')
            (proc/'202/exe').unlink();(proc/'202/exe').symlink_to('/usr/bin/python3.12')
            self.assertIsNone(_instance(proc,101))


if __name__=='__main__':unittest.main()

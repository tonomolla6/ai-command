"""Package replacement must not strand account launchers or cross-profile resume."""
import json
import os
from pathlib import Path
import pty
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ai_manager.core import Manager, ManagerError, read_json, write_json
from ai_manager.distribution import install
from ai_manager.providers import executable


class ExecutableRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.native=self.root/'native';self.native.mkdir()
        self.package=self.root/'package';self.package.mkdir()
        self.binary=self.package/'claude';self.binary.write_text('#!/bin/sh\nexit 0\n');self.binary.chmod(0o755)
        (self.native/'claude').symlink_to(self.binary)
        self.wrappers=self.root/'wrappers';self.wrappers.mkdir()
        (self.wrappers/'claude').write_text('#!/bin/sh\n# ai-manager provider entrypoint v1\nexit 1\n')
        (self.wrappers/'claude').chmod(0o755)
        self.env=patch.dict(os.environ,{'HOME':str(self.root),'PATH':str(self.wrappers)},clear=True)
        self.env.start()
        self.module=patch('ai_manager.providers.__file__',str(self.root/'ai_manager/providers.py'))
        self.module.start()

    def tearDown(self):
        self.module.stop();self.env.stop();self.temp.cleanup()

    def test_native_package_reappears_after_a_real_directory_replacement(self):
        displaced=self.root/'updating';self.package.rename(displaced)
        def restore():
            time.sleep(.15);displaced.rename(self.package)
        worker=threading.Thread(target=restore);worker.start()
        try:self.assertEqual(executable('claude'),str(self.native/'claude'))
        finally:worker.join(timeout=2)

    def test_present_binary_and_uninstalled_provider_never_wait(self):
        with patch('ai_manager.providers.time.sleep') as sleep:
            self.assertEqual(executable('claude'),str(self.native/'claude'))
            with self.assertRaises(ManagerError):executable('codex')
            sleep.assert_not_called()

    def test_permanently_broken_link_has_a_bounded_wait_and_clear_error(self):
        self.binary.unlink()
        with patch('ai_manager.providers.EXECUTABLE_RECOVERY_SECONDS',.03):
            with self.assertRaisesRegex(ManagerError,'actualización|actualizando'):
                executable('claude')

    def test_reappearing_managed_wrapper_is_never_used_as_native_binary(self):
        self.binary.write_text('#!/bin/sh\n# ai-manager provider entrypoint v1\nexit 1\n')
        with self.assertRaises(ManagerError):executable('claude')


class InstalledClaudeRecoveryTests(unittest.TestCase):
    def test_installed_new_and_cross_account_resume_survive_package_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);home=root/'home';home.mkdir();project=home/'project';project.mkdir()
            package=root/'package';package.mkdir();native=root/'native';native.mkdir()
            calls=root/'calls.json';binary=package/'claude'
            binary.write_text('#!'+sys.executable+'\n'+'''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if args==['auth','status','--json']:
    print(json.dumps({'loggedIn':True,'authMethod':'claude.ai','email':'user2@example.invalid'}))
elif args==['--version']:print('2.1.301')
else:
    path=Path(os.environ['CAPTURE']);calls=json.loads(path.read_text()) if path.exists() else []
    calls.append({'args':args,'home':os.environ.get('CLAUDE_CONFIG_DIR'),'cwd':os.getcwd(),
                  'sandbox':os.environ.get('IS_SANDBOX')})
    path.write_text(json.dumps(calls))
''');binary.chmod(0o755);(native/'claude').symlink_to(binary)
            env={'HOME':str(home),'PATH':str(native),'CAPTURE':str(calls),'TERM':'xterm-256color'}
            with patch.dict(os.environ,env,clear=True):
                manager=Manager();manager.config_dir.mkdir(parents=True);manager.state.mkdir(parents=True)
                accounts=[]
                for number in ('2','5'):
                    profile=home/('.claude-account-'+number);profile.mkdir()
                    a={'provider':'claude','account':number,'home':str(profile),'label':'Claude '+number,
                       'email':'user'+number+'@example.invalid','email_verified':True}
                    write_json(manager.credential(a),{'claudeAiOauth':{'accessToken':'test-only'}})
                    write_json(profile/'.claude.json',{'hasCompletedOnboarding':True})
                    accounts.append(a)
                write_json(manager.config_path,{'schema':1,'manager_schema':1,'accounts':accounts,'claude_skip_permissions':True})
                write_json(manager.state/'update-check.json',{'checked_at':time.time()})
                ident='11111111-2222-4333-8444-555555555555'
                transcript=Path(accounts[1]['home'])/'projects'/'project'/(ident+'.jsonl')
                transcript.parent.mkdir(parents=True)
                transcript.write_text(json.dumps({'sessionId':ident,'cwd':str(project)})+'\n')
                before={p:p.read_bytes() for p in (manager.config_path,transcript,
                        *[manager.credential(a) for a in accounts])}
                prefix=root/'prefix';install(Path(__file__).resolve().parent.parent,prefix)
                commands=(['claude2'],['claude2','--resume',ident],['c2','-r',ident],['c2r'])
                for argv in commands:
                    with self.subTest(argv=argv):
                        displaced=root/'updating';package.rename(displaced)
                        def restore():
                            time.sleep(.3);displaced.rename(package)
                        worker=threading.Thread(target=restore);worker.start()
                        master,slave=pty.openpty()
                        try:
                            result=subprocess.run([prefix/'bin'/argv[0],*argv[1:]],env=env,cwd=project,
                                                  stdin=slave,stdout=slave,stderr=slave,timeout=8)
                            self.assertEqual(result.returncode,0)
                        finally:
                            worker.join(timeout=2);os.close(slave);os.close(master)
                        call=read_json(calls)[-1]
                        self.assertEqual(call['home'],accounts[0]['home'])
                        self.assertEqual(call['cwd'],str(project));self.assertEqual(call['sandbox'],'1')
                        self.assertIn('--dangerously-skip-permissions',call['args'])
                        self.assertIn('bypassPermissions',call['args'])
                        if len(argv)>1 or argv[0]=='c2r':
                            self.assertEqual(call['args'][call['args'].index('--resume')+1],str(transcript))
                        else:self.assertNotIn('--resume',call['args'])
                for path,content in before.items():self.assertEqual(path.read_bytes(),content)

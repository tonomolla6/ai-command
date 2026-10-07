import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.core import Manager
from ai_manager.provider_updates import busy,configure_cron,run_updates,snapshot,restore_code,update_plan


class ProviderUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.manager=Manager(self.root/'home');self.manager.state.mkdir(parents=True)
        self.binary=self.root/'native';self.binary.write_text('original code');self.binary.chmod(0o755)
        self.plan={'provider':'agy','binary':self.binary,'path':self.binary,'command':['official','update']}

    def tearDown(self):self.temp.cleanup()

    def test_busy_detection_uses_process_identity_without_logging_arguments(self):
        proc=self.root/'proc';pid=proc/'99999999';pid.mkdir(parents=True)
        (pid/'exe').symlink_to(self.binary)
        (pid/'cmdline').write_bytes(b'private argument\0')
        self.assertTrue(busy(self.plan,proc))
        (pid/'exe').unlink();(pid/'exe').symlink_to('/usr/bin/other')
        (pid/'cmdline').write_bytes(b'/usr/bin/other\0')
        self.assertFalse(busy(self.plan,proc))

    def test_backup_deduplicates_and_restores_exact_binary_and_permissions(self):
        with patch('ai_manager.provider_updates.FLOOR',0):
            archive=snapshot(self.manager,self.plan,'1.0.0')
            self.assertEqual(snapshot(self.manager,self.plan,'1.0.0'),archive)
        self.assertEqual(archive.stat().st_mode & 0o777,0o600)
        self.binary.write_text('broken update');self.binary.chmod(0o700)
        restore_code(self.plan,archive)
        self.assertEqual(self.binary.read_text(),'original code');self.assertEqual(self.binary.stat().st_mode & 0o777,0o755)

    def test_running_codex_companion_also_blocks_native_updates(self):
        helper=self.root/'codex-code-mode-host'
        self.plan['companions']=[helper]
        proc=self.root/'proc';pid=proc/'99999999';pid.mkdir(parents=True)
        (pid/'exe').symlink_to(helper);(pid/'cmdline').write_bytes(b'private argument\0')
        self.assertTrue(busy(self.plan,proc))

    def test_failed_updater_restores_backup_and_does_not_export_raw_output(self):
        with patch('ai_manager.provider_updates.PROVIDERS',('agy',)), \
             patch('ai_manager.provider_updates.update_plan',return_value=self.plan), \
             patch('ai_manager.provider_updates.busy',return_value=False), \
             patch('ai_manager.provider_updates.current_version',side_effect=['1.0.0',None,'1.0.0']), \
             patch('ai_manager.provider_updates.FLOOR',0), \
             patch('ai_manager.provider_updates.subprocess.run',return_value=subprocess.CompletedProcess([],1,b'private-updater-data',b'private')):
            rows=run_updates(self.manager)
        self.assertEqual(rows[0]['status'],'RESTORED')
        self.assertNotIn('private-updater-data',str(rows));self.assertEqual(self.binary.read_text(),'original code')

    def test_codex_backup_restores_matching_companion_without_copying_unrelated_files(self):
        helper=self.root/'codex-code-mode-host';helper.write_text('original helper');helper.chmod(0o755)
        private=self.root/'auth.json';private.write_text('private fixture')
        self.plan.update(provider='codex',native=True,companions=[helper])
        with patch('ai_manager.provider_updates.FLOOR',0):
            archive=snapshot(self.manager,self.plan,'1.0.0')
            helper.write_text('different helper')
            changed=snapshot(self.manager,self.plan,'1.0.0')
        self.assertNotEqual(archive,changed)
        self.binary.write_text('different cli')
        restore_code(self.plan,archive)
        self.assertEqual(self.binary.read_text(),'original code')
        self.assertEqual(helper.read_text(),'original helper')
        self.assertEqual(helper.stat().st_mode & 0o777,0o755)
        self.assertEqual(private.read_text(),'private fixture')
        import tarfile
        with tarfile.open(archive) as tar:
            self.assertEqual(set(tar.getnames()),{'payload','companions/codex-code-mode-host'})

    def test_codex_restore_removes_only_companion_absent_in_snapshot(self):
        helper=self.root/'codex-code-mode-host'
        self.plan.update(provider='codex',native=True,companions=[helper])
        with patch('ai_manager.provider_updates.FLOOR',0):
            archive=snapshot(self.manager,self.plan,'1.0.0')
        helper.write_text('new helper');self.binary.write_text('new cli')
        restore_code(self.plan,archive)
        self.assertFalse(helper.exists());self.assertEqual(self.binary.read_text(),'original code')

    def test_busy_provider_is_skipped_without_backup_or_update(self):
        with patch('ai_manager.provider_updates.PROVIDERS',('agy',)), \
             patch('ai_manager.provider_updates.update_plan',return_value=self.plan), \
             patch('ai_manager.provider_updates.busy',return_value=True), \
             patch('ai_manager.provider_updates.subprocess.run') as execute:
            rows=run_updates(self.manager)
        self.assertEqual(rows[0]['status'],'BUSY');execute.assert_not_called()

    def test_npm_plan_targets_existing_prefix_not_managed_launchers(self):
        package=self.root/'prefix/lib/node_modules/@openai/codex';(package/'bin').mkdir(parents=True)
        (package/'package.json').write_text('{"name":"@openai/codex"}')
        binary=package/'bin/codex';binary.write_text('native')
        with patch('ai_manager.provider_updates.executable',return_value=str(binary)), \
             patch('ai_manager.provider_updates.shutil.which',return_value='/usr/bin/npm'):
            plan=update_plan('codex')
        self.assertEqual(plan['path'],package)
        self.assertEqual(plan['command'][plan['command'].index('--prefix')+1],str(self.root/'prefix'))

    def test_cron_uses_stable_launcher_private_logs_and_has_reversible_configuration(self):
        cron=self.root/'etc/cron.d/ai-command-providers';cron.parent.mkdir(parents=True)
        log=self.root/'log/ai-command/providers-update.log'
        rotate=self.root/'etc/logrotate.d/ai-command-providers'
        self.manager.config['command_bin']=str(self.root/'bin')
        with patch('ai_manager.provider_updates.CRON',cron),patch('ai_manager.provider_updates.LOG',log), \
             patch('ai_manager.provider_updates.ROTATE',rotate),patch('ai_manager.provider_updates.os.getuid',return_value=os.getuid()):
            if os.getuid()!=0:self.skipTest('root-specific system cron fixture')
            self.assertTrue(configure_cron(self.manager,'on'))
            self.assertIn('17 */6 * * * root',cron.read_text());self.assertIn('providers-update --scheduled',cron.read_text())
            self.assertEqual(log.stat().st_mode & 0o777,0o600)
            self.assertTrue(configure_cron(self.manager,'status'))
            self.assertFalse(configure_cron(self.manager,'off'));self.assertFalse(cron.exists())

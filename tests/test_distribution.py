import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.core import Manager, ManagerError, read_json, private_dir, write_json
from ai_manager.distribution import install, rollback, validate_release
from ai_manager.migrations import migrate, plan
from ai_manager.updater import extract_verified


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.source=self.root/'source';self.source.mkdir()
        repo=Path(__file__).resolve().parent.parent
        shutil.copytree(repo/'ai_manager',self.source/'ai_manager',ignore=shutil.ignore_patterns('__pycache__'))
        (self.source/'VERSION').write_text('1.0.0\n')
        self.prefix=self.root/'prefix'
        self.home=self.root/'home';self.home.mkdir()
        self.env=patch.dict(os.environ,{'HOME':str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop();self.temporary.cleanup()

    def test_atomic_update_rollback_preserves_credentials_and_config(self):
        credential=self.home/'.codex/auth.json';credential.parent.mkdir()
        credential.write_text('private-test-fixture')
        first=install(self.source,self.prefix)
        base=self.prefix/'lib/ai-command'
        self.assertEqual((base/'current').resolve().name,first['active'])
        self.assertEqual(subprocess.check_output([self.prefix/'bin/ai','--version'],text=True).strip(),'AI Command 1.0.0')
        (self.source/'VERSION').write_text('1.0.1\n')
        second=install(self.source,self.prefix)
        self.assertEqual(second['previous'],first['active'])
        self.assertEqual(rollback(base)['active'],first['active'])
        self.assertEqual(credential.read_text(),'private-test-fixture')
        self.assertFalse((self.home/'.config/ai-manager/config.json').exists())

    def test_failed_candidate_never_changes_current(self):
        first=install(self.source,self.prefix)
        (self.source/'VERSION').write_text('1.0.1')
        (self.source/'ai_manager/cli.py').write_text('raise RuntimeError("broken candidate")')
        with self.assertRaises(subprocess.CalledProcessError):install(self.source,self.prefix)
        self.assertEqual((self.prefix/'lib/ai-command/current').resolve().name,first['active'])

    def test_foreign_command_and_native_binary_are_preserved(self):
        bin_dir=self.prefix/'bin';bin_dir.mkdir(parents=True)
        native=bin_dir/'claude';native.write_text('native binary fixture')
        with self.assertRaises(ManagerError):install(self.source,self.prefix)
        self.assertEqual(native.read_text(),'native binary fixture')

    def test_native_symlink_target_survives_install_and_update(self):
        native=self.root/'claude-native';native.write_text('#!/bin/sh\necho native-fixture\n');native.chmod(0o755)
        bin_dir=self.prefix/'bin';bin_dir.mkdir(parents=True)
        (bin_dir/'claude').symlink_to(native)
        install(self.source,self.prefix)
        self.assertEqual(native.read_text(),'#!/bin/sh\necho native-fixture\n')
        self.assertEqual(subprocess.check_output([bin_dir/'claude','--version'],text=True).strip(),'native-fixture')

    def test_modified_previous_release_cannot_be_activated(self):
        first=install(self.source,self.prefix)
        (self.source/'VERSION').write_text('1.0.1')
        install(self.source,self.prefix)
        base=self.prefix/'lib/ai-command'
        (base/'releases'/first['active']/'ai_manager/cli.py').write_text('altered')
        with self.assertRaises(ManagerError):rollback(base)
        self.assertEqual(read_json(base/'install.json')['version'],'v1.0.1')

    def test_future_schema_refused_before_installation(self):
        m=Manager();private_dir(m.config_dir)
        write_json(m.config_path,{'schema':1,'manager_schema':999,'accounts':[]})
        with self.assertRaises(ManagerError):install(self.source,self.prefix)
        self.assertFalse((self.prefix/'lib/ai-command/current').exists())


class MigrationTests(unittest.TestCase):
    def test_idempotent_private_backup_and_preserves_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Manager(tmp);private_dir(m.config_dir)
            original={'schema':1,'accounts':[],'custom_future_field':{'keep':'yes'}}
            write_json(m.config_path,original)
            self.assertTrue(migrate(m,True))
            self.assertEqual(read_json(m.config_path),original)
            self.assertTrue(migrate(m))
            self.assertFalse(migrate(m))
            self.assertTrue(m.config['claude_skip_permissions'])
            self.assertEqual(m.config['custom_future_field'],original['custom_future_field'])
            backups=list((m.home/'.ai-manager/backups').glob('*/.config/ai-manager/config.json'))
            self.assertEqual(len(backups),1)
            self.assertEqual(read_json(backups[0]),original)

    def test_explicit_false_policy_is_never_enabled_by_upgrade(self):
        config,steps=plan({'schema':1,'accounts':[],'claude_skip_permissions':False})
        self.assertFalse(config['claude_skip_permissions'])


class ArchiveTests(unittest.TestCase):
    def blob(self,name,kind=tarfile.REGTYPE):
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w:gz') as tar:
            info=tarfile.TarInfo(name);info.type=kind
            if kind==tarfile.SYMTYPE:info.linkname='/tmp/forbidden'
            else:info.size=2
            tar.addfile(info,io.BytesIO(b'ok'))
        return output.getvalue()

    def test_bad_checksum_path_traversal_and_links_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name,kind in [('../escape',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),('link',tarfile.SYMTYPE)]:
                data=self.blob(name,kind)
                with self.assertRaises(ManagerError):extract_verified(data,hashlib.sha256(data).hexdigest(),Path(tmp))
            with self.assertRaises(ManagerError):extract_verified(self.blob('safe'),'0'*64,Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_valid_archive_is_extracted_only_after_checksum(self):
        data=self.blob('ai_manager/example.py')
        with tempfile.TemporaryDirectory() as tmp:
            extract_verified(data,hashlib.sha256(data).hexdigest(),Path(tmp))
            self.assertEqual((Path(tmp)/'ai_manager/example.py').read_text(),'ok')

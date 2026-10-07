import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from ai_manager.core import ManagerError
from ai_manager.native_updates import extract_binary,install_native,release_asset
from ai_manager.provider_updates import update_plan,run_updates,InsufficientSpace
from ai_manager.core import Manager


def archive(name='package/claude',content=b'new code',link=False):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as tar:
        member=tarfile.TarInfo(name);member.size=len(content)
        if link:member.type=tarfile.SYMTYPE;member.linkname='/outside';member.size=0
        tar.addfile(member,None if link else io.BytesIO(content))
    return output.getvalue()


class NativeUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.binary=self.root/'claude';self.binary.write_bytes(b'old code');self.binary.chmod(0o755)
        self.plan={'provider':'claude','path':self.binary,'binary':self.binary,'native':True}
        self.blob=archive()
        self.asset={'version':'2.0.1','url':'https://example.com/official.tgz','binary':'claude',
                    'algorithm':'sha512','digest':hashlib.sha512(self.blob).hexdigest()}

    def tearDown(self):self.temp.cleanup()

    def test_standalone_elf_is_maintainable_without_npm(self):
        self.binary.write_bytes(b'\x7fELFcode')
        with patch('ai_manager.provider_updates.executable',return_value=str(self.binary)):
            for provider in ('codex','claude','opencode'):
                self.assertTrue(update_plan(provider)['native'])

    def test_verified_candidate_replaces_only_native_binary_preserving_mode(self):
        sibling=self.root/'ai';sibling.write_text('managed launcher')
        auth=self.root/'auth.json';auth.write_text('private fixture')
        with patch('ai_manager.native_updates.release_asset',return_value=self.asset), \
             patch('ai_manager.native_updates.download',return_value=self.blob), \
             patch('ai_manager.native_updates.subprocess.run',return_value=subprocess.CompletedProcess([],0,'2.0.1','')):
            install_native(self.plan,'2.0.0',{})
        self.assertEqual(self.binary.read_bytes(),b'new code')
        self.assertEqual(self.binary.stat().st_mode & 0o777,0o755)
        self.assertEqual(sibling.read_text(),'managed launcher');self.assertEqual(auth.read_text(),'private fixture')

    def test_wrong_checksum_and_wrong_version_keep_existing_code(self):
        with patch('ai_manager.native_updates.release_asset',return_value=self.asset), \
             patch('ai_manager.native_updates.download',return_value=b'corrupted'):
            with self.assertRaises(ManagerError):install_native(self.plan,'2.0.0',{})
        with patch('ai_manager.native_updates.release_asset',return_value=self.asset), \
             patch('ai_manager.native_updates.download',return_value=self.blob), \
             patch('ai_manager.native_updates.subprocess.run',return_value=subprocess.CompletedProcess([],0,'2.0.0','')):
            with self.assertRaises(ManagerError):install_native(self.plan,'2.0.0',{})
        self.assertEqual(self.binary.read_bytes(),b'old code')

    def test_no_download_or_downgrade_when_current_version_is_newer(self):
        with patch('ai_manager.native_updates.release_asset',return_value=self.asset), \
             patch('ai_manager.native_updates.download') as download:
            install_native(self.plan,'2.1.0',{});download.assert_not_called()

    def test_archive_traversal_and_links_are_rejected(self):
        for name,link in (('../claude',False),('package/claude',True)):
            blob=archive(name,link=link);asset=dict(self.asset,digest=hashlib.sha512(blob).hexdigest())
            with self.assertRaises(ManagerError):extract_binary(blob,asset,self.root/'candidate')
        self.assertFalse((self.root/'candidate').exists())

    def test_arm64_codex_uses_exact_official_release_and_requires_digest(self):
        name='codex-aarch64-unknown-linux-musl.tar.gz'
        meta={'tag_name':'rust-v0.160.1','assets':[{'name':name,'digest':'sha256:'+'a'*64,
              'browser_download_url':'https://github.com/openai/codex/releases/download/rust-v0.160.1/'+name}]}
        with patch('ai_manager.native_updates.platform.machine',return_value='aarch64'), \
             patch('ai_manager.native_updates.platform.system',return_value='Linux'), \
             patch('ai_manager.native_updates.download',return_value=json.dumps(meta).encode()):
            self.assertEqual(release_asset('codex')['binary'],name[:-7])
            meta['assets'][0].pop('digest')
            with patch('ai_manager.native_updates.download',return_value=json.dumps(meta).encode()):
                with self.assertRaises(ManagerError):release_asset('codex')

    def test_claude_native_package_is_pinned_to_official_main_version(self):
        package='@anthropic-ai/claude-code-linux-arm64'
        main={'name':'@anthropic-ai/claude-code','version':'2.0.1','optionalDependencies':{package:'2.0.1'}}
        meta={'name':package,'version':'2.0.1','dist':{
              'tarball':'https://registry.npmjs.org/'+package+'/-/package.tgz',
              'integrity':'sha512-'+base64.b64encode(bytes.fromhex(self.asset['digest'])).decode()}}
        with patch('ai_manager.native_updates.platform.machine',return_value='aarch64'), \
             patch('ai_manager.native_updates.platform.system',return_value='Linux'), \
             patch('ai_manager.native_updates.platform.libc_ver',return_value=('glibc','2.34')), \
             patch('ai_manager.native_updates.download',side_effect=[json.dumps(main).encode(),json.dumps(meta).encode()]):
            self.assertEqual(release_asset('claude')['digest'],self.asset['digest'])

    def test_insufficient_disk_is_reported_and_never_runs_an_installer(self):
        manager=Manager(self.root/'home');manager.state.mkdir(parents=True)
        with patch('ai_manager.provider_updates.PROVIDERS',('claude',)), \
             patch('ai_manager.provider_updates.update_plan',return_value=self.plan), \
             patch('ai_manager.provider_updates.busy',return_value=False), \
             patch('ai_manager.provider_updates.current_version',return_value='2.0.0'), \
             patch('ai_manager.provider_updates.snapshot',side_effect=InsufficientSpace()), \
             patch('ai_manager.native_updates.install_native') as install:
            rows=run_updates(manager);install.assert_not_called()
        self.assertEqual(rows[0]['status'],'SKIPPED_SPACE')

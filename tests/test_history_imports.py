import gzip
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_manager.core import Manager, ManagerError
from ai_manager.history_imports import imported_sessions, restore_snapshot, imported_resume
from ai_manager.sessions import list_sessions


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.m=Manager(self.home)
        self.m.state.mkdir(parents=True)
        self.ident='11111111-2222-3333-4444-555555555555'
        self.account={'provider':'codex','account':'4','home':str(self.home/'.codex-account-4')}
        self.archive=self.home/'snapshots';self.archive.mkdir()
        self.data=(json.dumps({'type':'session_meta','payload':{'id':self.ident,'cwd':'/old/repo'}})+'\n').encode()
        with gzip.open(self.archive/'one.gz','wb') as f:f.write(self.data)
        row={'provider':'codex','account':'4','id':self.ident,'cwd':'/old/repo','updated':100,
             'relative_path':'2026/10/07/rollout-2026-10-07T00-00-00-'+self.ident+'.jsonl',
             'archive':'one.gz','bytes':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest()}
        manifest=self.archive/'manifest.json';manifest.write_text(json.dumps({'schema':1,'sessions':[row]}))
        self.m.config={'history_imports':[str(manifest)],'accounts':[self.account],
                       'cwd_aliases':{'/old':str(self.home)}}
        self.row=imported_sessions(self.m,'codex')[0]

    def test_snapshot_is_found_with_explicit_directory_mapping(self):
        rows=list_sessions(self.m,'codex',self.home/'repo')
        self.assertEqual([r['id'] for r in rows],[self.ident])
        self.assertEqual(list_sessions(self.m,'codex',self.home/'other'),[])
        self.assertEqual(list_sessions(self.m,'codex',None)[0]['cwd'],'/old/repo')

    def test_restore_is_verbatim_private_and_preserves_new_native_events(self):
        with patch('ai_manager.history_imports.shutil.disk_usage',return_value=SimpleNamespace(free=10**12)):
            target=restore_snapshot(self.m,self.account,self.row)
            self.assertEqual(target.read_bytes(),self.data);self.assertEqual(target.stat().st_mode&0o777,0o600)
            with target.open('ab') as f:f.write(b'{"new_event":true}\n')
            self.assertEqual(restore_snapshot(self.m,self.account,self.row),target)
            self.assertTrue(target.read_bytes().endswith(b'{"new_event":true}\n'))

    def test_dry_run_does_not_decompress_or_create_native_state(self):
        args=imported_resume(self.m,self.account,self.row,True)
        self.assertIn(self.ident,args);self.assertIn('resume',args)
        self.assertFalse(Path(self.account['home']).exists());self.assertFalse((self.m.state/'restored').exists())

    def test_corruption_never_creates_final_transcript(self):
        self.row['sha256']='0'*64
        with patch('ai_manager.history_imports.shutil.disk_usage',return_value=SimpleNamespace(free=10**12)):
            with self.assertRaises(ManagerError):restore_snapshot(self.m,self.account,self.row)
        self.assertFalse(any(self.home.rglob('rollout-*.jsonl')))
        self.assertFalse(any(self.home.rglob('.restore-*')))

    def test_insufficient_space_and_path_escape_are_rejected(self):
        with patch('ai_manager.history_imports.shutil.disk_usage',return_value=SimpleNamespace(free=100)):
            with self.assertRaises(ManagerError):restore_snapshot(self.m,self.account,self.row)
        self.row['relative_path']='../escape/'+self.ident+'.jsonl'
        with self.assertRaises(ManagerError):restore_snapshot(self.m,self.account,self.row,True)

    def test_manifest_cannot_point_to_foreign_snapshot(self):
        manifest=Path(self.m.config['history_imports'][0]);data=json.loads(manifest.read_text())
        data['sessions'][0]['archive']='../foreign.gz';manifest.write_text(json.dumps(data))
        with self.assertRaises(ManagerError):imported_sessions(self.m,'codex')

    def test_native_snapshot_restores_without_format_changes(self):
        path=self.archive/'native.jsonl';path.write_bytes(self.data)
        self.row.update(path=str(path),compression='none')
        with patch('ai_manager.history_imports.shutil.disk_usage',return_value=SimpleNamespace(free=10**12)):
            self.assertEqual(restore_snapshot(self.m,self.account,self.row).read_bytes(),self.data)

    def test_append_snapshot_preserves_base_and_checks_complete_checksum(self):
        base=self.archive/'base.jsonl';base.write_bytes(self.data)
        tail=b'{"type":"event_msg","payload":{"type":"task_complete"}}\n'
        with gzip.open(self.archive/'tail.gz','wb') as f:f.write(tail)
        self.row.update(path=str(self.archive/'tail.gz'),compression='append-gzip',base_archive='base.jsonl',
                        base_bytes=len(self.data),base_sha256=hashlib.sha256(self.data).hexdigest(),
                        bytes=len(self.data+tail),sha256=hashlib.sha256(self.data+tail).hexdigest())
        with patch('ai_manager.history_imports.shutil.disk_usage',return_value=SimpleNamespace(free=10**12)):
            self.assertEqual(restore_snapshot(self.m,self.account,self.row).read_bytes(),self.data+tail)
        self.assertEqual(base.read_bytes(),self.data)

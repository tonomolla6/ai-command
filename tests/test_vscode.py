import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from ai_manager.core import Manager
from ai_manager.vscode import install_extensions


class VSCodeInstallTests(unittest.TestCase):
    def test_update_replaces_legacy_registration_and_preserves_old_extension_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=Manager(tmp)
            root=manager.home/'.vscode-server/extensions';root.mkdir(parents=True)
            old='legacy-local.agentes-terminales-0.1.7'
            old_dir=root/old;old_dir.mkdir();(old_dir/'private-change.txt').write_text('preserved')
            registry=root/'extensions.json'
            registry.write_text(json.dumps([
                {'identifier':{'id':'legacy-local.agentes-terminales'},'version':'0.1.7','relativeLocation':old},
                {'identifier':{'id':'other.extension'},'version':'1.0.0'}]))
            with contextlib.redirect_stdout(io.StringIO()):install_extensions(manager)
            entries=json.loads(registry.read_text())
            ids=[e['identifier']['id'] for e in entries]
            self.assertNotIn('legacy-local.agentes-terminales',ids)
            self.assertIn('ai-command.agentes-terminales',ids)
            self.assertIn('other.extension',ids)
            self.assertTrue(json.loads((root/'.obsolete').read_text())[old])
            self.assertEqual((old_dir/'private-change.txt').read_text(),'preserved')
            with contextlib.redirect_stdout(io.StringIO()):install_extensions(manager)
            self.assertEqual(json.loads(registry.read_text()),entries)


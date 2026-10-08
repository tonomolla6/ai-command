import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from ai_manager.core import Manager
from ai_manager.vscode import install_extensions


class VSCodeInstallTests(unittest.TestCase):
    def test_install_disables_competing_native_revival_without_changing_other_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=Manager(tmp)
            settings=manager.home/'.vscode-server/data/Machine/settings.json'
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({'terminal.integrated.enablePersistentSessions':True,
                                           'editor.fontSize':19}))
            with contextlib.redirect_stdout(io.StringIO()):install_extensions(manager)
            result=json.loads(settings.read_text())
            self.assertFalse(result['terminal.integrated.enablePersistentSessions'])
            self.assertEqual(result['editor.fontSize'],19)

    def test_installed_extension_owns_its_terminal_helpers_and_preserves_path_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=Manager(tmp)
            unrelated=manager.home/'.local/bin/terminales'
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text('third-party terminal launcher\n')
            with contextlib.redirect_stdout(io.StringIO()):install_extensions(manager)
            root=manager.home/'.vscode-server/extensions'
            entry=next(e for e in json.loads((root/'extensions.json').read_text())
                       if e['identifier']['id']=='ai-command.terminales-persistentes')
            folder=root/entry['relativeLocation']
            source=Path(__file__).resolve().parent.parent/'integrations/vscode/bin'
            for name in ('terminales','terminales-native'):
                helper=folder/'bin'/name
                self.assertTrue(helper.is_file(), 'missing bundled helper: '+name)
                self.assertEqual(helper.read_bytes(),(source/name).read_bytes())
                self.assertEqual(helper.stat().st_mode & 0o777,0o755)
            self.assertEqual(unrelated.read_text(),'third-party terminal launcher\n')
            with contextlib.redirect_stdout(io.StringIO()):install_extensions(manager)

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

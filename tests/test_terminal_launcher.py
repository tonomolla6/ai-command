import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('bash') and shutil.which('flock'), 'Linux terminal helpers required')
class TerminalLauncherTests(unittest.TestCase):
    def run_launcher(self, root, script):
        binary=root/'bin/tmux';binary.parent.mkdir();binary.write_text(script);binary.chmod(0o755)
        launcher=Path(__file__).resolve().parent.parent/'integrations/vscode/bin/terminales'
        env={'HOME':str(root),'PATH':str(binary.parent)+':'+os.defpath,'TERM':'xterm-256color',
             'AI_COMMAND_TERMINALES_NATIVE':'1'}
        master,slave=pty.openpty()
        try:
            process=subprocess.Popen(['/bin/bash',str(launcher),'vsc-resume-'+'a'*32],
                                     stdin=slave,stdout=slave,stderr=subprocess.PIPE,cwd=root,env=env)
            os.close(slave);slave=None
            _,errors=process.communicate(timeout=5)
            return process.returncode,errors.decode()
        finally:
            if slave is not None:os.close(slave)
            os.close(master)

    def test_missing_recovery_never_creates_a_replacement_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            script='#!/bin/sh\ncase "$1" in new-session) touch "$HOME/unwanted-shell";; esac\n'
            code,error=self.run_launcher(root,script)
            self.assertEqual(code,69)
            self.assertIn('ai resume',error)
            self.assertFalse((root/'unwanted-shell').exists())

    def test_legacy_tmux_uses_a_classic_client_without_the_control_renderer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            script='''#!/bin/sh
case "$1" in
 list-sessions) printf '$7\\n';;
 show-option) printf '1\\n';;
 display-message) printf '3.4\\n';;
 attach-session) touch "$HOME/classic-client";;
 list-panes) touch "$HOME/unwanted-control-client"; exit 1;;
esac
'''
            code,error=self.run_launcher(root,script)
            self.assertEqual(code,0,error)
            self.assertTrue((root/'classic-client').exists())
            self.assertFalse((root/'unwanted-control-client').exists())

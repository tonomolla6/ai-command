"""Real, isolated tmux/PTY regressions; never touch the user's tmux server."""
import fcntl
import os
from pathlib import Path
import pty
import select
import shutil
import struct
import subprocess
import tempfile
import termios
import time
import unittest
import uuid


@unittest.skipUnless(shutil.which('tmux'), 'tmux required for transport integration')
class NativeTerminalTests(unittest.TestCase):
    def exercise(self, server_binary):
        socket='ai-test-'+uuid.uuid4().hex
        helper=Path(__file__).resolve().parent.parent/'integrations/vscode/bin/terminales-native'
        with tempfile.TemporaryDirectory(prefix='ai-native-test-') as tmp:
            root=Path(tmp)
            env={'HOME':tmp,'PATH':os.environ.get('PATH',os.defpath),'TERM':'xterm-256color',
                 'AI_COMMAND_TMUX_SOCKET':socket}
            config=root/'tmux.conf';config.write_text('set -g history-limit 1000\nset -g status off\nset -g window-size latest\n')
            def tmux(*args):
                return subprocess.check_output(['tmux','-L',socket,*args],env=env,text=True,stderr=subprocess.DEVNULL,timeout=3).strip()
            try:
                ident=subprocess.check_output([server_binary,'-L',socket,'-f',str(config),
                    'new-session','-d','-P','-F','#{session_id}','-s','fixture','-c',tmp,
                    '/bin/bash','--noprofile','--norc'],env=env,text=True,timeout=3).strip()
                original=tmux('display-message','-p','-t',ident+':','#{pane_pid}')
                for cycle in range(3):
                    master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,100,0,0))
                    proc=subprocess.Popen(['python3',str(helper),ident],stdin=slave,stdout=slave,stderr=subprocess.PIPE,env=env,start_new_session=True)
                    os.close(slave)
                    try:
                        data=b'';deadline=time.monotonic()+5
                        while b'\x1b[?2004h' not in data and time.monotonic()<deadline:
                            if select.select([master],[],[],.1)[0]:data+=os.read(master,65536)
                        self.assertIn(b'\x1b[?2004h',data,'native snapshot must render')
                        # Input is sent exclusively to this test-owned shell.
                        os.write(master,f'printf fixture-{cycle} > input-{cycle}\r'.encode())
                        marker=root/f'input-{cycle}';deadline=time.monotonic()+3
                        while not marker.exists() and time.monotonic()<deadline:
                            if select.select([master],[],[],.1)[0]:os.read(master,65536)
                        self.assertEqual(marker.read_text(),f'fixture-{cycle}')
                        # Rapid GUI resizes used to expose old control-server crashes.
                        for width in (100,70,120,90):
                            fcntl.ioctl(master,termios.TIOCSWINSZ,struct.pack('HHHH',24,width,0,0));time.sleep(.12)
                    finally:
                        proc.terminate()
                        try:_,error=proc.communicate(timeout=5)
                        except subprocess.TimeoutExpired:proc.kill();_,error=proc.communicate();raise
                        os.close(master)
                    self.assertEqual(proc.returncode,0,error.decode())
                    self.assertEqual(tmux('display-message','-p','-t',ident+':','#{pane_pid}'),original)
                    self.assertEqual(tmux('display-message','-p','-t',ident+':','#{pane_pipe}'),'0','owned pipe must be released')
                    self.assertEqual(tmux('list-clients','-F','#{client_pid}'),'','no client helper left behind')
            finally:
                subprocess.run(['tmux','-L',socket,'kill-server'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def test_current_tmux_reattaches_and_resizes_without_stopping_the_shell(self):
        self.exercise(shutil.which('tmux'))

    def test_legacy_server_uses_pipe_transport_and_survives_reconnections(self):
        path=Path('/usr/bin/tmux')
        if not path.exists():self.skipTest('separate legacy system tmux unavailable')
        version=subprocess.check_output([str(path),'-V'],text=True)
        if not version.startswith(('tmux 3.2','tmux 3.3','tmux 3.4','tmux 3.5')):self.skipTest('system server is already current')
        self.exercise(str(path))

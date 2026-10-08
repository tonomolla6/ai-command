"""Real PTY/process checks: responsive redraw, periodic reads and owned cleanup."""
import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import tempfile
import termios
import time
import unittest

from ai_manager.cli import limits,parser
from ai_manager.core import Manager,ManagerError,private_dir,write_json
from ai_manager.providers import Screen

ROOT=Path(__file__).resolve().parent.parent

WORKER=r'''
import json,sys,time,subprocess
from pathlib import Path
from ai_manager.core import Manager,write_json,now
m=Manager();events=m.state/'events.txt'
with events.open('a') as f:f.write('start '+str(time.monotonic())+'\n')
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
(m.state/'child.pid').write_text(str(child.pid))
mode=sys.argv[1]
if mode=='failure':child.terminate();child.wait();raise SystemExit(7)
time.sleep(1.1 if mode in ('periodic','interactive') else 60)
entries={m.key(a):{'provider':a['provider'],'account':a['account'],'status':'OK','queried_at':now(),
 'windows':[{'name':'codex/weekly','available_percent':73,'window_minutes':10080}],
 'reset_credits_available':2} for a in m.accounts('codex')}
m.save_limits(entries)
child.terminate();child.wait()
with events.open('a') as f:f.write('end '+str(time.monotonic())+'\n')
print('PRIVATE-PROVIDER-OUTPUT')
'''

RUNNER=r'''
import os,sys
sys.path.insert(0,sys.argv[1])
from ai_manager.cli import parser
from ai_manager.core import Manager
import ai_manager.monitoring as m
mode=sys.argv[2]
if mode=='periodic':m.INTERVAL=1
args=parser().parse_args(['usage','codex','--monitoring','--color','always'])
os.environ['AI_MANAGER_COLOR']=args.color
def probe(manager,provider):
 return m.Probe(manager,provider,command=[sys.executable,'-c',sys.argv[3],mode])
try:raise SystemExit(m.monitor(Manager(),args,probe_factory=probe))
except KeyboardInterrupt:raise SystemExit(130)
'''


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        self.manager=Manager(self.home);private_dir(self.manager.config_dir);private_dir(self.manager.state)
        accounts=[{'provider':'codex','account':str(n),'home':str(self.home/f'profile-{n}'),
                   'label':f'Fixture account {n}','email':f'user{n}@example.invalid'} for n in range(1,4)]
        write_json(self.manager.config_path,{'schema':1,'accounts':accounts})
        self.manager=Manager(self.home)
        self.manager.save_limits({self.manager.key(a):{'provider':'codex','account':a['account'],'status':'OK',
                    'queried_at':'2026-10-08T00:00:00+00:00','windows':[{'name':'codex/weekly',
                    'available_percent':99,'window_minutes':10080}]} for a in accounts})
        self.master,self.slave=pty.openpty()
        termios.tcsetwinsize(self.slave,(24,110));self.original_termios=termios.tcgetattr(self.slave)
        self.proc=None;self.output=b'';self.screen=Screen(rows=24,cols=110)

    def tearDown(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait()
        os.close(self.master);os.close(self.slave);self.tmp.cleanup()

    def start(self,mode):
        env={**os.environ,'HOME':str(self.home),'TERM':'xterm-256color','PYTHONPATH':str(ROOT)}
        env.pop('COLUMNS',None);env.pop('LINES',None)
        self.proc=subprocess.Popen([sys.executable,'-c',RUNNER,str(ROOT),mode,WORKER],
                                   stdin=self.slave,stdout=self.slave,stderr=self.slave,env=env,start_new_session=True)

    def pump(self,condition,timeout=5):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if select.select([self.master],[],[],.05)[0]:
                chunk=os.read(self.master,65536);self.output+=chunk;self.screen.feed(chunk)
            if condition():return
            if self.proc.poll() is not None:break
        self.fail('La condición de la pantalla/proceso no llegó a tiempo')

    def child_gone(self,pid):
        try:return (Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z'
        except FileNotFoundError:return True

    def test_parser_and_invalid_modes_do_not_spawn_or_query(self):
        self.assertTrue(parser().parse_args(['usage','claude','--monitoring']).monitoring)
        self.assertTrue(parser().parse_args(['limits','--monitoring']).monitoring)
        for argv in (['usage','--monitoring','--json'],['usage','--monitoring','--cached'],['usage','--monitoring']):
            with self.subTest(argv=argv),self.assertRaises(ManagerError):limits(self.manager,parser().parse_args(argv))
        self.assertFalse((self.manager.state/'events.txt').exists())

    def test_cached_cards_remain_visible_while_refresh_is_running_and_q_restores_terminal(self):
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
        try:
            self.start('slow')
            self.pump(lambda:'99% disponible' in self.screen.text() and (self.manager.state/'child.pid').exists())
            self.assertIn('Consultando en segundo plano',self.screen.text())
            owned=int((self.manager.state/'child.pid').read_text())
            stamp=time.monotonic();os.write(self.master,b'q');self.proc.wait(timeout=3)
            self.assertLess(time.monotonic()-stamp,3)
            self.assertEqual(self.proc.returncode,0);self.assertTrue(self.child_gone(owned))
            self.assertIsNone(unrelated.poll())
            self.assertEqual(termios.tcgetattr(self.slave),self.original_termios)
        finally:unrelated.terminate();unrelated.wait()

    def test_periodic_and_manual_refresh_are_silent_and_never_overlap(self):
        self.start('periodic')
        events=self.manager.state/'events.txt'
        self.pump(lambda:events.exists() and 'start ' in events.read_text())
        os.write(self.master,b'rrrr')
        self.pump(lambda:'73% disponible' in self.screen.text())
        self.pump(lambda:events.read_text().count('end ')>=2,timeout=6)
        pairs=[line.split() for line in events.read_text().splitlines()]
        self.assertGreaterEqual(len(pairs),4)
        self.assertEqual([row[0] for row in pairs[:4]],['start','end','start','end'])
        self.assertGreaterEqual(float(pairs[2][1]),float(pairs[1][1]))
        os.write(self.master,b'q');self.proc.wait(timeout=3)
        self.assertNotIn(b'PRIVATE-PROVIDER-OUTPUT',self.output)
        self.assertEqual(self.proc.returncode,0)
        self.assertEqual(termios.tcgetattr(self.slave),self.original_termios)

    def test_scroll_resize_and_manual_refresh_work_without_restarting_the_dashboard(self):
        self.start('interactive');events=self.manager.state/'events.txt'
        self.pump(lambda:'Actualizado ' in self.screen.text())
        os.write(self.master,b'G')
        self.pump(lambda:'Fixture account 3' in self.screen.text())
        count=events.read_text().count('start ')
        os.write(self.master,b'r')
        self.pump(lambda:events.read_text().count('start ')>count)
        termios.tcsetwinsize(self.slave,(18,78));self.proc.send_signal(signal.SIGWINCH)
        self.screen=Screen(rows=18,cols=78)
        os.write(self.master,b'g')
        self.pump(lambda:'Fixture account 1' in self.screen.text())
        self.assertIsNone(self.proc.poll())
        os.write(self.master,b'q');self.proc.wait(timeout=3)
        self.assertEqual(self.proc.returncode,0)

    def test_failure_keeps_cache_and_never_prints_provider_errors(self):
        self.start('failure')
        self.pump(lambda:'Consulta fallida' in self.screen.text())
        self.assertIn('99% disponible',self.screen.text())
        self.assertNotIn(b'PRIVATE-PROVIDER-OUTPUT',self.output)
        self.assertEqual(self.manager.cache()['accounts']['codex:1']['windows'][0]['available_percent'],99)
        os.write(self.master,b'q');self.proc.wait(timeout=3)

    def test_sigterm_restores_terminal_and_retires_detached_query_children(self):
        self.start('slow');child=self.manager.state/'child.pid'
        self.pump(child.exists);owned=int(child.read_text())
        self.proc.send_signal(signal.SIGTERM);self.proc.wait(timeout=3)
        self.assertEqual(self.proc.returncode,130);self.assertTrue(self.child_gone(owned))
        self.assertEqual(termios.tcgetattr(self.slave),self.original_termios)

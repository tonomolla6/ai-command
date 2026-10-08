"""A terminal dashboard with one owned, silent quota query at a time."""
from __future__ import annotations

import contextlib
import curses
import datetime as dt
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .core import Manager, ManagerError, private_dir
from .single_tools import current_tools
from .ui import ANSI, colored, fit, usage_lines, visible_width

INTERVAL = 300
PROBE_TIMEOUT = 120


def _process_tree(parent):
    """Read PID/parent/start-time only; never inspect arguments or environment."""
    processes={}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            fields=(path/'stat').read_text().rsplit(')',1)[1].split()
            processes[int(path.name)]=(int(fields[1]),fields[19])
        except (OSError,ValueError,IndexError):continue
    owned={parent}
    while True:
        children={pid for pid,(ppid,_) in processes.items() if ppid in owned}
        if children<=owned:break
        owned|=children
    return {pid:processes[pid][1] for pid in owned if pid in processes}


def _signal_process(pid,start,sig):
    """Use a pidfd and recheck identity to avoid touching a reused PID."""
    fd=None
    try:fd=os.pidfd_open(pid)
    except (OSError,AttributeError):pass
    try:
        fields=(Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()
        if fields[19]==start:
            if fd is not None:signal.pidfd_send_signal(fd,sig)
            else:os.kill(pid,sig)
    except (OSError,IndexError):pass
    finally:
        if fd is not None:os.close(fd)


class Probe:
    def __init__(self,manager,provider=None,command=None):
        private_dir(manager.state)
        argv=command or [sys.executable,'-m','ai_manager.cli','usage',
                         *([provider] if provider else []),'--refresh','--json']
        env=dict(os.environ)
        env['PYTHONPATH']=str(Path(__file__).resolve().parent.parent)
        self.process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL,env=env,cwd=manager.state,
                                      start_new_session=True)
        self.started=time.monotonic()
        self.identity=(Path('/proc')/str(self.process.pid)/'stat').read_text().rsplit(')',1)[1].split()[19]

    def poll(self):
        code=self.process.poll()
        if code is None and time.monotonic()-self.started>=PROBE_TIMEOUT:
            self.close();return 124
        return code

    def close(self):
        if self.process.poll() is not None:return
        # Freeze only this query's parent while collecting its descendants.
        # Codex/PTY probes can own separate sessions, so killing a single group
        # would leave them behind. Live user agents are outside this tree.
        _signal_process(self.process.pid,self.identity,signal.SIGSTOP)
        owned={}
        for _ in range(5):
            found=_process_tree(self.process.pid)
            if found.get(self.process.pid)!=self.identity:break
            new={pid:start for pid,start in found.items() if pid not in owned}
            if not new:break
            for pid,start in new.items():_signal_process(pid,start,signal.SIGSTOP)
            owned.update(new)
        for pid,start in owned.items():
            _signal_process(pid,start,signal.SIGTERM)
            _signal_process(pid,start,signal.SIGCONT)
        try:self.process.wait(timeout=.3)
        except subprocess.TimeoutExpired:pass
        for pid,start in owned.items():_signal_process(pid,start,signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):self.process.wait(timeout=1)


def snapshot(manager,provider):
    accounts=manager.accounts(provider)+[a for a in current_tools(manager)
                                         if provider is None or a['provider']==provider]
    entries=manager.cache().get('accounts',{})
    rows=[entries.get(manager.key(a),{'provider':a['provider'],'account':a['account'],
                                    'status':'UNKNOWN','windows':[]}) for a in accounts]
    return accounts,rows


def _draw_line(screen,y,text,width,pairs):
    text=fit(text,max(1,width-1));x=0;attr=0;last=0
    for match in list(ANSI.finditer(text))+[None]:
        end=match.start() if match else len(text)
        part=text[last:end]
        if part:
            with contextlib.suppress(curses.error):screen.addstr(y,x,part,attr)
            x+=visible_width(part)
        if not match:break
        for code in map(int,match.group()[2:-1].split(';')):
            if code==0:attr=0
            elif code==1:attr|=curses.A_BOLD
            elif code==90:attr=(attr&curses.A_BOLD)|curses.A_DIM
            elif code in pairs:attr=(attr&curses.A_BOLD)|pairs[code]
        last=match.end()


def monitor(manager,args,probe_factory=Probe):
    if args.json or args.cached:
        raise ManagerError('--monitoring no admite --json ni --cached')
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ManagerError('--monitoring necesita una terminal interactiva')
    if os.environ.get('TERM','dumb')=='dumb':
        raise ManagerError('--monitoring necesita un TERM compatible, por ejemplo xterm-256color')
    from .cli import cached_age,format_reset

    def dashboard(screen):
        with contextlib.suppress(curses.error):curses.curs_set(0)
        screen.keypad(True);screen.timeout(200)
        pairs={}
        if curses.has_colors():
            curses.start_color()
            with contextlib.suppress(curses.error):curses.use_default_colors()
            for n,code in enumerate(range(31,37),1):
                curses.init_pair(n,code-30,-1);pairs[code]=curses.color_pair(n)
        _,old_mouse=curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(0)
        probe=None;next_query=time.monotonic();offset=0;last_render=None
        message='Datos de caché · consulta inicial pendiente'
        accounts,rows=snapshot(manager,getattr(args,'provider',None))
        try:
            while True:
                stamp=time.monotonic()
                if probe is not None:
                    code=probe.poll()
                    if code is not None:
                        probe.close();probe=None
                        manager_now=Manager(manager.home)
                        accounts,rows=snapshot(manager_now,getattr(args,'provider',None))
                        message=('Actualizado '+dt.datetime.now().astimezone().strftime('%H:%M:%S %Z') if code==0 else
                                 'Consulta fallida · datos de caché; se reintentará')
                        last_render=None
                if probe is None and stamp>=next_query:
                    try:probe=probe_factory(manager,getattr(args,'provider',None))
                    except (OSError,ManagerError):message='No se pudo consultar · datos de caché; se reintentará'
                    next_query=stamp+INTERVAL;last_render=None
                height,width=screen.getmaxyx()
                countdown=max(0,int(next_query-stamp))
                status=('Consultando en segundo plano · datos anteriores visibles' if probe is not None else message)+\
                       f' · próxima en {countdown//60:02d}:{countdown%60:02d}'
                # Use the same cards and layout as the normal command. Curses
                # updates changed cells, without writing a new screen to scrollback.
                lines=usage_lines(accounts,rows,format_reset,cached_age,getattr(args,'layout','auto'))[1:-1]
                page=max(1,height-3);offset=max(0,min(offset,max(0,len(lines)-page)))
                frame=(height,width,offset,int(stamp),status)
                if frame!=last_render:
                    screen.erase()
                    _draw_line(screen,0,colored('AI USAGE · MONITORING · cada 5 minutos','title'),width,pairs)
                    if height>1:_draw_line(screen,1,colored(status,'low' if probe is not None else 'muted'),width,pairs)
                    for y,line in enumerate(lines[offset:offset+page],2):
                        if y<height-1:_draw_line(screen,y,line,width,pairs)
                    if height>2:
                        footer=f'q salir · r actualizar · ↑↓/rueda desplazar · PgUp/PgDn · {offset+1}/{max(1,len(lines))}'
                        _draw_line(screen,height-1,colored(footer,'muted'),width,pairs)
                    screen.noutrefresh();curses.doupdate();last_render=frame
                key=screen.getch()
                if key in (ord('q'),ord('Q'),27):return 0
                if key in (ord('r'),ord('R')) and probe is None:next_query=0
                elif key in (curses.KEY_UP,ord('k')):offset-=1
                elif key in (curses.KEY_DOWN,ord('j')):offset+=1
                elif key==curses.KEY_PPAGE:offset-=page
                elif key in (curses.KEY_NPAGE,ord(' ')):offset+=page
                elif key in (curses.KEY_HOME,ord('g')):offset=0
                elif key in (curses.KEY_END,ord('G')):offset=len(lines)
                elif key==curses.KEY_MOUSE:
                    with contextlib.suppress(curses.error):
                        _,__,___,____,buttons=curses.getmouse()
                        if buttons&curses.BUTTON4_PRESSED:offset-=3
                        elif buttons&getattr(curses,'BUTTON5_PRESSED',0):offset+=3
        finally:
            curses.mousemask(old_mouse)
            if probe is not None:probe.close()

    handlers={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGHUP)}
    def interrupted(signum,frame):raise KeyboardInterrupt
    try:
        for sig in handlers:signal.signal(sig,interrupted)
        return curses.wrapper(dashboard)
    except curses.error:
        raise ManagerError('No se pudo preparar la pantalla de monitoring') from None
    finally:
        for sig,handler in handlers.items():signal.signal(sig,handler)

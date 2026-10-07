"""Official CLI adapters. Never call private HTTP endpoints or request a model turn."""
from __future__ import annotations

import codecs
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import ManagerError, now


AUTO_WRAPPER_MARKER = b"# ai-manager provider entrypoint v1"


def executable(provider):
    # Skip our entrypoints for probes, login and account-bound launches. Keep
    # the original installation path/symlink so provider updates still work.
    # A system installation can keep its original ARM64 binaries next to the
    # package. Each user's HOME/auth remains independent of the global code.
    directories=[str(Path(__file__).resolve().parent.parent/'native'),*os.get_exec_path()]
    for directory in directories:
        path = Path(directory or os.curdir).absolute() / provider
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        try:
            with path.open('rb') as stream:
                if AUTO_WRAPPER_MARKER in stream.read(256):
                    continue
        except OSError:
            continue
        return str(path)
    raise ManagerError(f"No se encuentra el ejecutable original de {provider} en PATH")


def version(provider):
    try:
        env=dict(os.environ)
        if provider=='agy':env['AGY_CLI_DISABLE_AUTO_UPDATE']='1'
        result = subprocess.run([executable(provider), "--version"], capture_output=True,
                                text=True, timeout=10,env=env)
        return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"
    except (OSError, subprocess.TimeoutExpired, ManagerError):
        return "UNKNOWN"


def stop_process(proc):
    """Stop only the subprocess group we created, including its local helpers."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    # The leader can exit before a helper; still retire this owned process group.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=3)


class CodexRPC:
    def __init__(self, manager, account, timeout=18):
        self.timeout = timeout
        self.buffer = b""
        self.counter = 0
        command=[executable("codex"), "app-server", "--listen", "stdio://"]
        if account.get('shared_sqlite_home'):
            command.extend(['-c','sqlite_home='+json.dumps(account['shared_sqlite_home'])])
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=manager.env(account), cwd=str(manager.state), start_new_session=True)

    def __enter__(self):
        try:
            self.call("initialize", {"clientInfo": {"name": "ai_manager", "version": "1.0.0"}})
            self.send({"method": "initialized", "params": {}})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        stop_process(self.proc)
        self.proc.stdin.close()
        self.proc.stdout.close()

    def send(self, value):
        self.proc.stdin.write((json.dumps(value) + "\n").encode())
        self.proc.stdin.flush()

    def call(self, method, params=None):
        self.counter += 1
        ident = self.counter
        self.send({"id": ident, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == ident:
                    if "error" in msg:
                        # Error text can include identity, URLs or credentials. Keep only code.
                        raise ManagerError(f"Codex RPC {method}: error {msg['error'].get('code', 'UNKNOWN')}")
                    return msg.get("result", {})
                if "id" in msg and "method" in msg:
                    self.send({"id": msg["id"], "error": {"code": -32601, "message": "Read-only client"}})
            if select.select([self.proc.stdout], [], [], max(0, deadline-time.monotonic()))[0]:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise ManagerError("Codex app-server terminó sin respuesta")
                self.buffer += chunk
                if len(self.buffer) > 4 * 1024 * 1024:
                    raise ManagerError("Respuesta Codex demasiado grande")
        raise ManagerError("Timeout consultando Codex app-server")


def available(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if not 0 <= value <= 100:
        return None
    return round(100 - value, 1)


def reset_value(value):
    try:
        if isinstance(value, (float, int)):
            return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()
        if isinstance(value, str):
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.isoformat() if parsed.tzinfo else None
    except (OverflowError, ValueError, OSError):
        pass
    return None


def parse_codex(value):
    buckets = value.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        bucket = value.get("rateLimits")
        buckets = {bucket.get("limitId") or "codex": bucket} if isinstance(bucket, dict) else {}
    windows = []
    for bucket_id, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        for key in ("primary", "secondary"):
            window = bucket.get(key)
            if not isinstance(window, dict):
                continue
            percent = available(window.get("usedPercent"))
            mins = window.get("windowDurationMins")
            name = {300: "5h", 10080: "weekly"}.get(mins, f"{mins}m" if isinstance(mins, int) else key)
            windows.append({"name": f"{bucket_id}/{name}", "available_percent": percent,
                            "reset_at": reset_value(window.get("resetsAt")), "window_minutes": mins})
    return windows


def codex_credits(value):
    buckets=value.get('rateLimitsByLimitId') or {'codex':value.get('rateLimits')}
    result=[]
    for name,bucket in buckets.items():
        if not isinstance(bucket,dict) or not isinstance(bucket.get('credits'),dict):continue
        credits=bucket['credits'];balance=credits.get('balance')
        try:
            numeric=float(balance)
            balance=str(balance) if math.isfinite(numeric) and 0<=numeric<1e20 else None
        except (ValueError,TypeError):balance=None
        result.append({'bucket':name,'has_credits':credits.get('hasCredits'),
                       'unlimited':credits.get('unlimited') is True,'balance':balance,'unit':'provider credits'})
    return result


def claude_credits(text):
    compact=re.sub(r'\s+','',text).lower()
    if 'usagecreditsareoff' in compact or 'extrausageisoff' in compact:
        return {'enabled':False,'balance':None,'display':'Desactivados','source':'claude /usage'}
    marker=re.search(r'(?:Usage\s*credits|Extra\s*usage)(.*)',text,re.I|re.S)
    if marker:
        body=marker[1][:1500]
        balance=re.search(r'(?:balance|remaining|available)\s*[:·]?\s*([$€£]?)\s*(\d+(?:[.,]\d+)?)',body,re.I)
        return {'enabled':True if balance else None,'balance':balance[2] if balance else None,
                'currency_symbol':balance[1] if balance else None,
                'display':None if balance else 'UNKNOWN: saldo no mostrado por /usage','source':'claude /usage'}
    return {'enabled':None,'balance':None,'display':'UNKNOWN','source':'claude /usage'}


class Screen:
    """Small VT screen reader for read-only slash commands, including split escapes."""
    def __init__(self, rows=60, cols=160):
        self.rows, self.cols = rows, cols
        self.lines = [[" "] * cols for _ in range(rows)]
        self.x = self.y = 0
        self.saved = (0, 0)
        self.pending = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def text(self):
        return "\n".join("".join(row).rstrip() for row in self.lines)

    def feed(self, data):
        text = self.pending + self.decoder.decode(data)
        self.pending = ""
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x1b":
                if i+1 >= len(text):
                    self.pending = text[i:]; break
                if text[i+1] == "[":
                    match = re.match(r"\x1b\[([0-?]*)([ -/]*)([@-~])", text[i:])
                    if not match:
                        self.pending = text[i:]; break
                    self.csi(match[1], match[3])
                    i += len(match[0]); continue
                if text[i+1] in "]P":
                    match = re.search(r"\x07|\x1b\\", text[i+2:])
                    if not match:
                        self.pending = text[i:]; break
                    i += 2 + match.end(); continue
                if text[i+1] == "7": self.saved = self.x, self.y
                if text[i+1] == "8": self.x, self.y = self.saved
                i += 2; continue
            if ch == "\r": self.x = 0
            elif ch == "\n":
                self.y += 1
                if self.y >= self.rows:
                    self.lines.pop(0); self.lines.append([" "]*self.cols); self.y = self.rows-1
            elif ch == "\b": self.x = max(0, self.x-1)
            elif ch == "\t": self.x = min(self.cols-1, (self.x//8+1)*8)
            elif ch >= " " and ch != "\x7f":
                if self.x >= self.cols: self.x = 0; self.y = min(self.rows-1, self.y+1)
                self.lines[self.y][self.x] = ch
                self.x += 1
            i += 1

    def csi(self, raw, command):
        vals = [int(v) if v.isdigit() else 0 for v in raw.lstrip("?<>=").split(";")]
        n = vals[0] or 1
        if command in "Hf": self.y, self.x = n-1, (vals[1] or 1)-1 if len(vals)>1 else 0
        elif command == "A": self.y -= n
        elif command in "Be": self.y += n
        elif command in "Ca": self.x += n
        elif command == "D": self.x -= n
        elif command in "G`": self.x = n-1
        elif command == "d": self.y = n-1
        elif command == "E": self.y += n; self.x = 0
        elif command == "F": self.y -= n; self.x = 0
        elif command == "s": self.saved = self.x, self.y
        elif command == "u": self.x, self.y = self.saved
        elif command == "J":
            if vals[0] in (2,3): self.lines = [[" "]*self.cols for _ in range(self.rows)]
            elif vals[0] == 0:
                self.lines[self.y][self.x:] = [" "]*(self.cols-self.x)
                for y in range(self.y+1,self.rows): self.lines[y] = [" "]*self.cols
        elif command == "K":
            start,end = (0,self.cols) if vals[0]==2 else ((0,self.x+1) if vals[0]==1 else (self.x,self.cols))
            self.lines[self.y][start:end] = [" "]*(end-start)
        elif command == "X": self.lines[self.y][self.x:min(self.cols,self.x+n)] = [" "]*min(n,self.cols-self.x)
        self.x = max(0,min(self.cols-1,self.x)); self.y = max(0,min(self.rows-1,self.y))


def parse_claude(text):
    windows = {}
    label = None
    for line in text.splitlines():
        line = line.strip().strip("│┃ ")
        compact = re.sub(r"\s+", "", line).lower()
        if re.match(r"current(?:session|week|day|month)", compact):
            label = re.sub(r"\s+", " ", re.split(r':\s*(?=\d)',line,maxsplit=1)[0])[:90]
            label = re.sub(r"^Current\s*session", "Current session", label, flags=re.I)
            label = re.sub(r"^Current\s*week", "Current week", label, flags=re.I)
            windows[label] = {"name": label, "available_percent": None, "reset_at": None}
        if label:
            match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(used|left|remaining|available)", line, re.I)
            if match:
                used = float(match[1]) if match[2].lower()=="used" else 100-float(match[1])
                windows[label]["available_percent"] = available(used)
            match = re.search(r"\bResets?\s*(.+)", line, re.I)
            if match:
                # Preserve the CLI's timezone and wording rather than guessing a date.
                windows[label]["reset_display"] = match[1].strip()[:120]
            if compact.startswith(("usagecredits", "extrausage", "what'scontributing", "last24h")):
                label = None
    return [v for v in windows.values() if v["available_percent"] is not None]


def claude_reset_at(window, reference):
    """Resolve only recognized /usage dates with an explicit IANA timezone.

    Keep reset_display verbatim. Missing/ambiguous dates remain UNKNOWN rather
    than borrowing the host timezone or guessing a weekly reset day.
    """
    match = re.fullmatch(
        r'\s*(?:(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2})\s*,?\s*(?:at\s*)?)?'
        r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(([^()\s]+)\)\s*',
        window.get('reset_display', ''), re.I)
    if not match: return None
    kind = re.sub(r'\s+', '', window.get('name', '')).lower()
    horizon = next((minutes for name, minutes in [('session',300),('week',10080),('day',1440),('month',46080)]
                    if kind.startswith('current'+name)), None)
    if horizon is None or (not match[1] and not kind.startswith(('currentsession','currentday'))):
        return None
    try:
        ref = dt.datetime.fromisoformat(reference)
        if ref.tzinfo is None: return None
        zone = ZoneInfo(match[6]); local = ref.astimezone(zone)
        hour = int(match[3]); minute = int(match[4] or 0)
        if not 1 <= hour <= 12 or minute > 59: return None
        hour = hour % 12 + (12 if match[5].lower() == 'pm' else 0)
        if match[1]:
            months = {name: i for i,name in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split(),1)}
            dates = []
            for year in (local.year, local.year+1):
                try: dates.append(dt.datetime(year, months[match[1].lower()], int(match[2]), hour, minute, tzinfo=zone))
                except ValueError: continue
        else:
            today = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            dates = [today, today+dt.timedelta(days=1)]
        for date in dates:
            seconds = date.timestamp()-ref.timestamp()
            if not 0 < seconds <= (horizon+120)*60: continue
            # DST clock gaps and repeated local hours have no unambiguous reset.
            if date.replace(fold=0).utcoffset() != date.replace(fold=1).utcoffset(): continue
            utc = date.astimezone(dt.timezone.utc)
            if utc.astimezone(zone).replace(tzinfo=None) != date.replace(tzinfo=None): continue
            return utc.isoformat()
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        pass
    return None


def parse_claude_report(report):
    """Public CLI stream-json usage_report. Percent is used, never remaining."""
    rates=report.get('rate_limits') if isinstance(report,dict) else None
    if not isinstance(rates,dict):return None
    windows=[]
    for limit in rates.get('limits') or []:
        if not isinstance(limit,dict):continue
        value=available(limit.get('percent'))
        if value is None:continue
        kind=limit.get('kind');scope=limit.get('scope') or {}
        label={'session':'Current session','weekly_all':'Current week (all models)'}.get(kind)
        if kind=='weekly_scoped':
            model=scope.get('model') or {}
            label='Current week ('+str(model.get('display_name') or 'scoped')+')'
        windows.append({'name':label or str(kind or limit.get('group') or 'Limit'),
                        'available_percent':value,'reset_at':reset_value(limit.get('resets_at')),
                        'window_minutes':300 if kind=='session' else 10080 if str(kind).startswith('weekly') else None})
    extra=rates.get('extra_usage')
    credits={'enabled':None,'balance':None,'display':'UNKNOWN','source':'claude /usage stream-json'}
    if isinstance(extra,dict):
        credits.update(enabled=extra.get('is_enabled'),
                       display='Desactivados' if extra.get('is_enabled') is False else 'Saldo no publicado · extra uso activo',
                       monthly_limit=extra.get('monthly_limit'),used_credits=extra.get('used_credits'),
                       utilization=extra.get('utilization'),currency=extra.get('currency'))
    return {'windows':windows,'credits':credits,'source':'claude --print /usage (stream-json, 0 turns)'} if windows else None


def claude_usage(manager, account, timeout=25, details=False):
    # This installed release registers /usage as supportsNonInteractive. Do not
    # guess that an unknown release handles slash commands without inference.
    if not re.search(r'\b2\.1\.284\b',version('claude')):
        return claude_usage_pty(manager,account,timeout,details)
    command=[executable('claude'),'--safe-mode','--strict-mcp-config','--mcp-config',
             '{"mcpServers":{}}','--tools','','--permission-mode','plan',
             '--no-session-persistence','--print','--verbose','--output-format','stream-json','/usage']
    result=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,text=True,
                          env=manager.env(account),cwd=manager.state,timeout=timeout)
    reports=[];finish=None
    for line in result.stdout.splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if not isinstance(event,dict):continue
        if event.get('type')=='result':finish=event
        if isinstance(event.get('usage_report'),dict):reports.append(event['usage_report'])
    if (result.returncode or not finish or finish.get('subtype')!='success' or finish.get('is_error')
            or finish.get('num_turns')!=0 or finish.get('total_cost_usd')!=0):
        raise ManagerError('Claude /usage no confirmó una consulta local sin turnos; no se reintenta')
    for report in reversed(reports):
        parsed=parse_claude_report(report)
        if parsed:return parsed if details else parsed['windows']
    windows=parse_claude(finish.get('result',''))
    if windows:
        parsed={'windows':windows,'credits':claude_credits(finish['result']),
                'source':'claude --print /usage (text, 0 turns)'}
        return parsed if details else windows
    raise ManagerError('Claude /usage: no hay ventanas reconocibles en el comando oficial')


def claude_usage_pty(manager, account, timeout=22, details=False):
    command = [executable("claude"), "--safe-mode", "--strict-mcp-config", "--mcp-config",
               '{"mcpServers":{}}', "--tools", "", "--permission-mode", "plan", "/usage"]
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 60, 160, 0, 0))
    env = manager.env(account)
    env.update({"TERM": "xterm-256color", "DISABLE_AUTOUPDATER": "1", "NO_COLOR": "1"})
    # A private, non-repository working directory avoids project hooks/settings.
    proc = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                            cwd=manager.state, env=env, start_new_session=True)
    os.close(slave)
    screen = Screen()
    raw = b""
    best = []
    stable_since = time.monotonic()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], .2)[0]:
                try: chunk = os.read(master, 65536)
                except OSError: break
                if not chunk: break
                raw = (raw+chunk)[-512*1024:]
                if b"\x1b[6n" in raw[-1000:]: os.write(master, b"\x1b[1;1R")
                if b"\x1b[c" in raw[-1000:]: os.write(master, b"\x1b[?1;2c")
                screen.feed(chunk)
                found = parse_claude(screen.text())
                if found and found != best:
                    best = found; stable_since = time.monotonic()
                if re.search(r'choose the text style|to change this later, run /theme',screen.text(),re.I) and not best:
                    raise ManagerError('Claude sigue mostrando el primer inicio; revisa ai login claude '+account['account'])
                if re.search(r'trust this|trust this folder|quick safety check',screen.text(),re.I) and not best:
                    raise ManagerError('Claude espera confirmación de confianza de carpeta; el login está verificado')
                if re.search(r"select.*theme|choose.*theme|sign in|log in",screen.text(),re.I) and not best:
                    # Never accept login, trust or other prompts by simulated keystrokes.
                    raise ManagerError("Claude requiere onboarding/login manual: abre ai claude " + account["account"])
            if best and time.monotonic()-stable_since > 2 and all(w.get("reset_display") for w in best):
                return {'windows':best,'credits':claude_credits(screen.text())} if details else best
            if proc.poll() is not None: break
        if best:return {'windows':best,'credits':claude_credits(screen.text())} if details else best
        raise ManagerError("UNKNOWN: /usage no devolvió ventanas reconocibles (timeout/formato/autenticación)")
    finally:
        stop_process(proc)
        os.close(master)
        # Never persist raw PTY output, which can contain identity or login links.


def auth_status(manager, account):
    if account.get('single'):
        from .single_tools import current_status
        return current_status(manager,account)
    if not manager.has_auth(account):
        return False, "SIN LOGIN"
    provider = account["provider"]
    command = ([executable("codex"), "login", "status"] if provider=="codex" else
               [executable("claude"), "auth", "status", "--json"])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=12,
                                env=manager.env(account), cwd=manager.state)
        if provider == "claude":
            data = json.loads(result.stdout)
            ready = data.get("loggedIn") is True
        else:
            ready = result.returncode == 0
        return ready, "OK" if ready else "REVISAR LOGIN"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False, "UNKNOWN"


def query_limits(manager, account):
    if account.get('single'):
        from .single_tools import query_usage
        return query_usage(manager,account)
    entry = {"provider": account["provider"], "account": account["account"],
             "email":account.get('email'),"email_verified":False,
             "queried_at": now(), "available_percent": None, "windows": [], "credits":None,"status": "UNKNOWN"}
    if not manager.has_auth(account):
        entry["reason"] = "SIN LOGIN"
        return entry
    try:
        with manager.lock("probe-"+manager.key(account).replace(":","-"), timeout=15):
            if account["provider"] == "codex":
                with CodexRPC(manager, account) as rpc:
                    actual=rpc.call('account/read',{'refreshToken':False}).get('account') or {}
                    observed=actual.get('email')
                    if account.get('email') and observed and account['email'].lower()!=observed.lower():
                        raise ManagerError('El correo del login no coincide con la cuenta registrada')
                    if observed:entry.update(email=observed,email_verified=True)
                    raw=rpc.call("account/rateLimits/read")
                    windows = parse_codex(raw)
                    entry['credits']=codex_credits(raw)
                    reset_credits=raw.get('rateLimitResetCredits')
                    if isinstance(reset_credits,dict):entry['reset_credits_available']=reset_credits.get('availableCount')
                entry["source"] = "codex app-server account/rateLimits/read"
            else:
                # auth status is official, non-interactive and avoids inferring identity
                # from a shared transcript or stale global config.
                result=subprocess.run([executable('claude'),'auth','status','--json'],capture_output=True,
                                      text=True,timeout=12,env=manager.env(account),cwd=manager.state)
                actual=json.loads(result.stdout);observed=actual.get('email')
                if result.returncode or actual.get('loggedIn') is not True:
                    raise ManagerError('Claude no confirma el login; revisa ai login claude '+account['account'])
                if account.get('email') and observed and account['email'].lower()!=observed.lower():
                    raise ManagerError('El correo del login no coincide con la cuenta registrada')
                if observed:entry.update(email=observed,email_verified=True)
                from .claude_setup import repair_onboarding
                repair_onboarding(manager,account,auth=actual)
                data = claude_usage(manager, account,details=True)
                windows=data['windows'];entry['credits']=data['credits']
                for window in windows:
                    if not window.get('reset_at'):window['reset_at']=claude_reset_at(window,entry['queried_at'])
                entry["source"] = data.get('source','claude /usage (PTY, safe-mode, no tools)')
        if not windows:
            raise ManagerError("No hay límites reconocibles para esta autenticación")
        entry["windows"] = windows
        values = [w["available_percent"] for w in windows if w["available_percent"] is not None]
        entry["available_percent"] = min(values) if values else None
        entry["status"] = "OK" if values else "UNKNOWN"
        entry["last_success_at"] = entry["queried_at"]
    except (ManagerError, OSError, ValueError, subprocess.SubprocessError) as exc:
        # Only our fixed diagnostic messages; never persist raw exception/server output.
        entry["reason"] = str(exc) if isinstance(exc, ManagerError) else type(exc).__name__
    return entry

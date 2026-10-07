"""Private state, credential isolation, and conservative secret detection."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time


class ManagerError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise ManagerError(f"Directorio privado enlazado: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise ManagerError(f"Propietario inesperado: {path}")
    path.chmod(0o700)
    return path


def atomic_write(path, text, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise ManagerError(f"No se sobrescribe un symlink: {path}")
    fd, name = tempfile.mkstemp(prefix=".ai-write-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, value):
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


# These patterns recognize values, not merely words such as "token" in instructions.
# Sharing is fail-closed on unreadable/oversized files. No detector can prove that
# arbitrary historical prose has never included an unlabelled secret.
SECRET = re.compile(
    r"(?:sk-(?:ant-|proj-|svcacct-)?[A-Za-z0-9_-]{18,}"
    r"|(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|(?:Bearer\s+)[A-Za-z0-9._~+/-]{16,}"
    r"|https?://[^\s/:@]+:[^\s/@]{3,}@"
    r"|(?:access[_-]?token|refresh[_-]?token|api[_-]?key|password|passwd|"
    r"client[_-]?secret|authorization|cookie|secret[_-]?key|token)"
    r"[\s\"'\\]*[:=][\s\"'\\]*[^\s\"'\\,;{}<>]{12,})",
    re.IGNORECASE,
)
SENSITIVE_PATH = re.compile(
    r"(?:^|/)(?:\.env(?:\..*)?|auth\.json|\.credentials\.json|\.claude\.json|"
    r"pass(?:word)?s?\.txt|cookies?(?:\..*)?|credentials?(?:\..*)?|"
    r"secrets?|id_rsa|id_ed25519|[^/]*\.(?:pem|key|p12|pfx|kdbx))$", re.I
)


def safe_file(path, max_bytes=32 * 1024 * 1024):
    path = Path(path)
    if SENSITIVE_PATH.search(path.name) or path.is_symlink() or not path.is_file():
        return False
    try:
        if path.stat().st_size > max_bytes:
            return False
        tail = ""
        with path.open(errors="replace") as stream:
            while chunk := stream.read(256 * 1024):
                text = tail + chunk
                if SECRET.search(text) or "\x00" in text:
                    return False
                tail = text[-4096:]
        return True
    except OSError:
        return False


def public_text(text):
    """Redact whole lines containing sensitive values; strip terminal controls."""
    lines = []
    for line in str(text).splitlines():
        labelled_secret = re.search(
            r"(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|"
            r"secret[_-]?key|client[_-]?secret|authorization|cookie)"
            r"[\s\"']*[:=][\s\"']*\S+", line, re.I)
        if SECRET.search(line) or labelled_secret or SENSITIVE_PATH.search(line.strip()):
            lines.append("[REDACTED]")
        else:
            lines.append(re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", line)[:800])
    return "\n".join(lines)


def backup_files(home, paths, purpose):
    root = private_dir(home / ".ai-manager" / "backups")
    dest = root / dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    private_dir(dest)
    names = []
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        if not path.is_file():
            raise ManagerError(f"Backup selectivo espera un archivo: {path}")
        target = dest / path.relative_to(home)
        private_dir(target.parent)
        shutil.copy2(path, target, follow_symlinks=True)
        target.chmod(0o600)
        names.append(str(path))
    write_json(dest / "manifest.json", {"created_at": now(), "purpose": purpose, "files": names})
    return dest


class Manager:
    def __init__(self, home=None):
        self.home = Path(home or Path.home())
        self.config_dir = self.home / ".config/ai-manager"
        self.state = self.home / ".local/state/ai-manager"
        self.config_path = self.config_dir / "config.json"
        self.config = read_json(self.config_path)

    def require_setup(self):
        if self.config.get("schema") != 1:
            raise ManagerError("Gestor sin configurar: ejecuta ai setup")
        private_dir(self.state)

    def command_bin(self):
        return self.config.get('command_bin') or ("/usr/local/bin" if os.getuid()==0 else str(self.home/'.local/bin'))

    def accounts(self, provider=None, include_inactive=False):
        return [a for a in self.config.get("accounts", []) if (provider is None or a["provider"] == provider)
                and (include_inactive or a.get('enabled',True))]

    def account(self, provider, number, allow_inactive=False):
        for a in self.accounts(provider,include_inactive=True):
            if a["account"] == str(number) or a.get('email','').lower() == str(number).lower():
                if not allow_inactive and not a.get('enabled',True):
                    raise ManagerError(f"{a['label']} está desactivada. ai enable {provider} {a['account']}")
                return a
        raise ManagerError(f"Cuenta inexistente: {provider} {number}")

    @staticmethod
    def key(account):
        return f"{account['provider']}:{account['account']}"

    def credential(self, account):
        return Path(account["home"]) / ("auth.json" if account["provider"] == "codex" else ".credentials.json")

    def has_auth(self, account):
        data = read_json(self.credential(account))
        if account["provider"] == "codex":
            return bool(data.get("tokens") or data.get("OPENAI_API_KEY"))
        return bool(data.get("claudeAiOauth") or data.get("anthropicApiKey"))

    def env(self, account):
        env = dict(os.environ)
        # Parent agent routing and auth must never silently replace the selected account.
        for key in list(env):
            if (key.startswith(("CODEX_", "CLAUDE_", "ANTHROPIC_", "OPENAI_"))
                    or key in {"CLAUDE_CONFIG_DIR"}):
                env.pop(key)
        if account["provider"] == "codex":
            env["CODEX_HOME"] = account["home"]
        elif not account.get("legacy"):
            env["CLAUDE_CONFIG_DIR"] = account["home"]
        # Keep the real HOME, cwd, Git identity, shell, and project instruction files.
        return env

    @contextlib.contextmanager
    def lock(self, name, blocking=True, timeout=None):
        private_dir(self.state)
        path = self.state / (name + ".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            deadline=time.monotonic()+timeout if timeout is not None else None
            while True:
                try:
                    fcntl.flock(fd,fcntl.LOCK_EX | (fcntl.LOCK_NB if not blocking or deadline is not None else 0))
                    break
                except BlockingIOError:
                    if deadline is None or time.monotonic()>=deadline:
                        raise ManagerError("Otra operación ai utiliza esta cuenta/sesión; espera a que termine.") from None
                    time.sleep(min(.05,max(0,deadline-time.monotonic())))
            yield
        finally:
            os.close(fd)

    def cache(self):
        return read_json(self.state / "limits.json", {"schema": 1, "accounts": {}})

    def save_limits(self, entries):
        with self.lock("limits"):
            data = self.cache()
            data.setdefault("accounts", {}).update(entries)
            write_json(self.state / "limits.json", data)

    def record_launch(self, account, cwd):
        with self.lock("launches"):
            path = self.state / "projects.json"
            data = read_json(path)
            data[str(cwd)] = {"provider": account["provider"], "account": account["account"], "last_launch": now()}
            write_json(path, data)

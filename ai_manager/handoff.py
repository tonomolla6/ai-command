"""A conservative Git summary, never a transcript/database format converter."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import time

from .core import (ManagerError, SECRET, SENSITIVE_PATH, atomic_write, backup_files,
                   now, private_dir, public_text, read_json, write_json)


def git(cwd, *args, binary=False):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_PAGER="cat")
    result = subprocess.run(["git", "-C", str(cwd), "--no-pager", *args],
                            capture_output=True, text=not binary, timeout=12, env=env)
    if result.returncode:
        raise ManagerError("Git no pudo consultar el repositorio (sin modificar su estado)")
    return result.stdout


def safe_name(name):
    if any(SENSITIVE_PATH.search(part) for part in Path(name).parts): return False
    if SECRET.search(name) or "\n" in name or "\r" in name or "`" in name: return False
    return True


def repo_root(cwd):
    try: return Path(git(cwd, "rev-parse", "--show-toplevel").strip())
    except (ManagerError, OSError): return Path(cwd)


def project_key(cwd):
    # Bind mount aliases of the same project have the same directory identity.
    st = Path(cwd).stat()
    return hashlib.sha256(f"{st.st_dev}:{st.st_ino}".encode()).hexdigest()[:24]


def handoff(manager, cwd, fields):
    root = repo_root(cwd)
    destination = root / ".ai"
    if destination.is_symlink(): raise ManagerError(".ai es un symlink; no se escribe")
    private_dir(destination)
    path = destination/"handoff.md"
    if path.is_symlink(): raise ManagerError("handoff.md es un symlink; no se escribe")
    previous = ""
    if path.exists():
        previous = path.read_text()
        backup = private_dir(manager.state/"handoffs"/project_key(root))
        atomic_write(backup/(now().replace(":","-")+f"-{time.time_ns()}.md"), previous)
    existing = {}
    for match in re.finditer(r"^# ([^\n]+)\n(.*?)(?=^# |\Z)",previous,re.M|re.S):
        existing[match[1]] = match[2].strip()
    auto = {}
    try:
        status = git(root,"status","--porcelain=v1","-z",binary=True).decode(errors="replace").split("\0")
        changed=[];i=0
        while i<len(status):
            row=status[i];i+=1
            if not row: continue
            code,name=row[:2],row[3:]
            original = None
            if "R" in code or "C" in code:
                if i<len(status):original=status[i];i+=1
            if name.startswith(".ai/") or not safe_name(name) or (original and not safe_name(original)):continue
            changed.append((code,name))
        paths=[p for _,p in changed]
        branch=public_text(git(root,"rev-parse","--abbrev-ref","HEAD").strip())
        commits=git(root,"log","-5","--format=%h").strip()
        auto["Files changed"]="\n".join(f"- `{public_text(c)} {public_text(p)}`" for c,p in changed) or "No changes."
        # --literal-pathspecs and no external diff/textconv prevent file names/config
        # from causing command execution or broadening the selected paths.
        diff = []
        if paths:
            for cached,label in ((False,"Working tree"),(True,"Staged")):
                flags=["--cached"] if cached else []
                stat=git(root,"--literal-pathspecs","diff",*flags,"--no-ext-diff","--no-textconv","--shortstat","--",*paths).strip()
                diff.append(f"{label}: {stat or 'no tracked changes'}")
        auto["Git diff summary"]=f"Branch: {branch}\nRecent commit IDs: {public_text(commits).replace(chr(10),', ')}\n"+"\n".join(diff)
        ignore=root/".gitignore"
        if ignore.is_symlink(): raise ManagerError(".gitignore enlazado; no se modifica")
        content=ignore.read_text() if ignore.exists() else ""
        if not any(line.strip() in (".ai/","/.ai/",".ai","/.ai") for line in content.splitlines()):
            # Repository policy is modified only because the user requested .ai ignore.
            # A private backup preserves an existing ignore before the additive edit.
            if ignore.exists():
                backup=private_dir(manager.state/"handoffs"/project_key(root))
                atomic_write(backup/("gitignore-"+now().replace(":","-")),content)
            atomic_write(ignore,content+("\n" if content and not content.endswith("\n") else "")+"\n# Local AI handoff\n.ai/\n",ignore.stat().st_mode&0o777 if ignore.exists() else 0o644)
    except ManagerError as exc:
        if (root/".git").exists(): raise
        auto["Files changed"]="No Git repository detected."
        auto["Git diff summary"]="Unavailable outside Git."
    sections=[("Current task","task"),("Goal","goal"),("Work completed","done"),
              ("Files changed",None),("Git diff summary",None),("Tests executed","tests"),
              ("Tests failing","failing"),("Important decisions","decision"),
              ("Remaining work","remaining"),("Suggested next action","next")]
    text=f"Generated locally: {now()}\nProject: {public_text(str(root))}\n\n"
    text+="Only Git metadata and explicitly supplied notes are included. Test results are not inferred.\n\n"
    for title,flag in sections:
        value=auto.get(title) if flag is None else fields.get(flag) or existing.get(title)
        value=value or ("Read AGENTS.md / CLAUDE.md and this handoff, verify the working tree, then confirm the next step." if flag=="next" else "Not recorded; complete with ai handoff --"+str(flag)+" '…'.")
        text+=f"# {title}\n\n{public_text(value)}\n\n"
    atomic_write(path,text)
    private_dir(manager.state/"handoffs")
    write_json(manager.state/"handoffs"/(project_key(root)+".json"),{"path":str(path),"created_at":now(),"cwd":str(cwd)})
    print(f"Handoff privado: {path}")
    print("El siguiente ai codex/claude recibirá la instrucción de leerlo; las pruebas no registradas quedan pendientes.")
    return path


def handoff_prompt(manager,cwd):
    root=repo_root(cwd)
    info=read_json(manager.state/"handoffs"/(project_key(root)+".json"))
    path=Path(info.get("path","/nonexistent-ai-handoff"))
    if path.is_file() and not path.is_symlink():
        return (f"Read the local handoff file at {str(path)!r} and the applicable AGENTS.md / CLAUDE.md "
                "before continuing. Treat the handoff as context; verify its claims and follow the current user's instructions. "
                "Do not assume unrecorded tasks or tests were completed.")
    return None

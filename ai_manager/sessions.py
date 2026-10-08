"""Read-only session discovery across homes; resume through provider CLIs."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess

from .core import ManagerError, backup_files, private_dir, safe_file
from .history_imports import imported_sessions, mapped_directory, imported_resume


def claude_resume_request(arguments):
    """Recognize native UUID resumes without treating prompts/searches as flags."""
    args = list(arguments)
    for index, arg in enumerate(args):
        if arg == '--':
            break
        if index and args[index - 1] in ('-p', '--print', '--model', '--system-prompt',
                '--append-system-prompt', '--settings', '--mcp-config', '--agent', '--session-id'):
            continue
        if arg in ('--continue', '-c'):
            return True, None, args[:index] + args[index + 1:]
        value = None
        count = 1
        if arg in ('--resume', '-r') and index + 1 < len(args):
            value = args[index + 1]
            count = 2
        elif arg.startswith('--resume='):
            value = arg.partition('=')[2]
        if value and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
            return True, value.lower(), args[:index] + args[index + count:]
    return False, None, args


def shared_claude_arguments(manager, arguments):
    """Native noninteractive calls can also read the original account's transcript."""
    requested, ident, extras = claude_resume_request(arguments)
    if not requested or not ident:
        return list(arguments)
    session = next((s for s in list_sessions(manager, 'claude', None) if s['id'] == ident), None)
    return ['--resume', session['path'], *extras] if session else list(arguments)


def same_directory(left, right):
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.abspath(left) == os.path.abspath(right)


def codex_sessions(home, sqlite_home=None):
    found = {}
    # No writes, migrations, token reads or database copying. Tolerate schema changes.
    for db in sorted(Path(sqlite_home or home).glob("state_*.sqlite"), reverse=True):
        try:
            with contextlib.closing(sqlite3.connect(db.as_uri()+"?mode=ro", uri=True, timeout=2)) as conn:
                cols = {r[1] for r in conn.execute("pragma table_info(threads)")}
                if not {"id", "rollout_path", "cwd", "updated_at"} <= cols: continue
                recency = 'coalesce(recency_at,updated_at)' if 'recency_at' in cols else 'updated_at'
                query = "select id,rollout_path,cwd,"+recency + (",source" if "source" in cols else ",''") + " from threads"
                if "archived" in cols: query += " where archived = 0"
                if "has_user_event" in cols: query += " and has_user_event = 1" if "where" in query else " where has_user_event = 1"
                for ident, path, cwd, updated, source in conn.execute(query):
                    if "subagent" in (source or '').lower() or not path: continue
                    path = Path(path)
                    if path.is_file():
                        found[ident] = {"id": ident, "path": str(path), "cwd": cwd,
                                        "updated": float(updated or path.stat().st_mtime)}
            break
        except (sqlite3.Error, OSError, ValueError):
            continue
    # Scan only metadata, never the 16 GB of tool output in the existing history.
    for path in (home / "sessions").glob("**/*.jsonl"):
        try:
            with path.open() as stream:
                line = stream.readline(1024*1024)
            data = json.loads(line)
            meta = data.get("payload", {})
            if (data.get("type") != "session_meta" or meta.get('parent_thread_id') or
                    "subagent" in str(meta.get("source", "")).lower()): continue
            ident = meta.get("id") or meta.get("session_id")
            if ident and ident not in found:
                found[ident] = {"id": ident, "path": str(path), "cwd": meta.get("cwd", ""),
                                "updated": path.stat().st_mtime}
        except (OSError, ValueError):
            continue
    return list(found.values())


def claude_sessions(home):
    found = []
    for path in (home / "projects").glob("*/*.jsonl"):
        try:
            # Initial queue records need not have cwd; inspect a bounded prefix.
            with path.open() as stream:
                for _, line in zip(range(50), stream):
                    if len(line)>1024*1024: break
                    try: data = json.loads(line)
                    except ValueError: continue
                    if data.get("isSidechain"): break
                    if data.get("cwd"):
                        found.append({"id": data.get("sessionId") or path.stem,
                                      "path": str(path), "cwd": data["cwd"],
                                      "updated": path.stat().st_mtime})
                        break
        except OSError:
            continue
    return found


def list_sessions(manager, provider, cwd):
    found = {}
    for account in manager.accounts(provider):
        home = Path(account["home"])
        rows = codex_sessions(home, account.get("shared_sqlite_home")) if provider=="codex" else claude_sessions(home)
        for row in rows:
            if cwd is not None and not same_directory(mapped_directory(manager,row["cwd"]), cwd): continue
            row["provider"] = provider
            row["origin_account"] = account["account"]
            if row["id"] not in found or row["updated"] > found[row["id"]]["updated"]:
                found[row["id"]] = row
    for name in manager.config.get('history_homes', {}).get(provider, []):
        home=Path(name)
        rows=codex_sessions(home) if provider=='codex' else claude_sessions(home)
        for row in rows:
            if cwd is not None and not same_directory(mapped_directory(manager,row['cwd']),cwd):continue
            row.update(provider=provider,origin_account='existente')
            if row['id'] not in found or row['updated']>found[row['id']]['updated']:found[row['id']]=row
    for row in imported_sessions(manager,provider):
        if cwd is not None and not same_directory(mapped_directory(manager,row['cwd']),cwd):continue
        native=found.get(row['id'])
        if native:row['updated']=max(row['updated'],native['updated'])
        # Keep the import's native-index routing after subsequent local turns.
        found[row['id']]=row
    return sorted(found.values(), key=lambda r:r["updated"], reverse=True)


def prepare_resume(manager, account, session, dry_run=False):
    if session.get('snapshot_manifest'):
        return imported_resume(manager,account,session,dry_run)
    source = Path(session["path"])
    home = Path(account["home"])
    provider = account["provider"]
    if provider == "claude":
        # Documented absolute transcript path support avoids symlinking private
        # projects, .claude.json, plans, tasks, snapshots or authentication.
        return ["--resume", str(source)]
    if account.get("shared_sqlite_home"):
        # The official CLI resolves the ID and rollout path in the common native
        # state store, including paginated history. Never alter creator identity.
        return ["resume", session["id"]]
    try:
        source.resolve().relative_to((home/"sessions").resolve())
        return ["resume", session["id"]]
    except ValueError:
        pass
    if not safe_file(source, max_bytes=512*1024*1024):
        raise ManagerError("La sesión Codex contiene posibles secretos, es ilegible o demasiado grande; "
                           "no se enlaza. Selecciona otra sesión (--pick) o usa ai handoff.")
    relative = None
    for original in manager.accounts("codex"):
        try:
            relative = source.relative_to(Path(original["home"])/"sessions")
            break
        except ValueError:
            continue
    if relative is None or ".." in relative.parts:
        raise ManagerError("Transcript fuera de los homes registrados")
    target = home / "sessions" / relative
    # Only directories created by this task; never follow directory symlinks.
    parent = home
    for part in target.parent.relative_to(home).parts:
        parent = parent / part
        private_dir(parent)
    if target.is_symlink():
        if target.resolve() != source.resolve():
            raise ManagerError("Enlace de sesión existente apunta a otro archivo")
    elif target.exists():
        raise ManagerError("Ya existe otra copia de esta sesión; no se sobrescribe")
    else:
        backup_files(manager.home, [home/"config.toml"], "Before adding a scanned Codex transcript link")
        target.symlink_to(source.resolve())
    return ["resume", session["id"]]


def session_lock_name(provider, ident):
    return "session-" + hashlib.sha256((provider+":"+ident).encode()).hexdigest()[:24]

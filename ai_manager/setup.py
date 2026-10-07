"""Idempotent installation without moving or copying provider credentials."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tomllib

from .core import (ManagerError, SECRET, atomic_write, backup_files, private_dir,
                   safe_file, write_json, now)


def toml_dump(data):
    def scalar(value):
        if isinstance(value, str): return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool): return "true" if value else "false"
        if isinstance(value, (int,float)): return str(value)
        if isinstance(value,list): return "["+", ".join(scalar(v) for v in value)+"]"
        if isinstance(value,dict): return "{"+", ".join(json.dumps(k)+" = "+scalar(v) for k,v in value.items())+"}"
        raise ManagerError("Tipo TOML no soportado en configuración")
    lines = []
    def table(value, path):
        if path: lines.append("["+".".join(json.dumps(k) for k in path)+"]")
        for k,v in value.items():
            if not isinstance(v,dict): lines.append(json.dumps(k)+" = "+scalar(v))
        for k,v in value.items():
            if isinstance(v,dict): lines.append(""); table(v,path+[k])
    table(data,[])
    text = "\n".join(lines)+"\n"
    tomllib.loads(text)
    return text


def clean_settings(data):
    blocked = {"oauthaccount", "apikey", "apikeyhelper", "http_headers", "bearer_token",
               "access_token", "refresh_token", "client_secret", "credentials", "auth",
               "mcpservers", "mcp_servers", "hooks", "env", "shell_environment_policy"}
    def clean(obj):
        if isinstance(obj,dict):
            return {k:clean(v) for k,v in obj.items() if k.lower() not in blocked and
                    not any(word in k.lower() for word in ("token", "password", "secret"))}
        if isinstance(obj,list): return [clean(v) for v in obj if not isinstance(v,str) or not SECRET.search(v)]
        if isinstance(obj,str) and SECRET.search(obj): return "[REDACTED]"
        return obj
    return clean(data)


def setup(manager):
    if manager.config_path.exists():
        manager.require_setup()
        print("Configuración existente conservada.")
        return
    private_dir(manager.config_dir)
    private_dir(manager.state)
    legacy_paths = [manager.home/".codex/config.toml", manager.home/".claude.json",
                    manager.home/".claude/settings.json", manager.home/".claude/settings.local.json"]
    backup = backup_files(manager.home, legacy_paths, "AI manager setup: preserve existing configuration")
    accounts = []
    codex_original = manager.home/".codex"
    original_config = codex_original/"config.toml"
    original_data = tomllib.loads(original_config.read_text()) if original_config.exists() else {}
    shared_sqlite = str(Path(original_data.get("sqlite_home", codex_original)).expanduser().absolute())
    audit = []
    for provider,count in (("codex",2),("claude",3)):
        original = manager.home/("."+provider)
        for number in range(1,count+1):
            profile = manager.home/f".{provider}-account-{number}"
            if profile.exists():
                raise ManagerError(f"Perfil preexistente sin registrar: {profile}; no se modifica")
            private_dir(profile)
            # Account 1 intentionally keeps the running legacy home. The numbered
            # directory is a descriptor, NOT an alias of a credential directory.
            legacy = number==1 and original.exists()
            home = original if legacy else profile
            entry = {"provider":provider,"account":str(number),"label":f"{provider.title()} {number}",
                     "home":str(home),"profile":str(profile),"legacy":legacy}
            if provider=="codex":entry["shared_sqlite_home"]=shared_sqlite
            accounts.append(entry)
            write_json(profile/"profile.json", {"effective_home":str(home),"legacy":legacy,
                                                "note":"Credentials are never copied or linked"})
            if legacy: continue
            if provider=="codex":
                source = original/"config.toml"
                data = tomllib.loads(source.read_text()) if source.exists() else {}
                data = clean_settings(data)
                data["cli_auth_credentials_store"] = "file"
                # Do not inherit settings that pin runtime/database/daemon to C1.
                for key in ("sqlite_home","log_dir","model_catalog_json","hooks"):
                    data.pop(key,None)
                # Supported runtime state location; credentials remain under CODEX_HOME.
                # Includes modern paginated thread_history, not just legacy JSONL.
                data["sqlite_home"]=shared_sqlite
                atomic_write(home/"config.toml", toml_dump(data))
                names = ["AGENTS.md", "rules", "skills"]
            else:
                source = original/"settings.json"
                data = json.loads(source.read_text()) if source.exists() else {}
                write_json(home/"settings.json", clean_settings(data))
                names = ["CLAUDE.md", "rules", "skills", "agents", "commands", "output-styles"]
            # Share only individually scanned instruction files. Never whole homes,
            # .claude.json, projects, auth, plugins, tasks, plans, databases or logs.
            for name in names:
                source = original/name
                files = sorted(source.rglob("*")) if source.is_dir() else [source]
                for file in files:
                    if not file.exists() or file.is_dir(): continue
                    approved = safe_file(file, max_bytes=2*1024*1024)
                    audit.append({"provider":provider,"account":str(number),
                                  "relative_path":str(file.relative_to(original)),"shared":approved})
                    if not approved: continue
                    target = home/file.relative_to(original)
                    private_dir(target.parent)
                    target.symlink_to(file.resolve())
    config = {"schema":1,"created_at":now(),"accounts":accounts,
              "history":"Codex official sqlite_home; Claude federated discovery and official path resume",
              "backup":str(backup)}
    write_json(manager.config_path, config)
    write_json(manager.state/"sharing-audit.json", {"files":audit,"checked_at":now()})
    manager.config=config
    print(f"5 perfiles preparados. Backup privado: {backup}")
    print("Cuentas 1 conservan ~/.codex y ~/.claude; las restantes usan sus homes numerados.")
    print("Las credenciales existentes no se han copiado ni movido.")


def install_commands(source, bin_dir, accounts=None):
    from .distribution import installation, launcher_contents, own_launcher
    try:base=installation(source)
    except ManagerError:base=None
    if base is not None and Path(bin_dir).absolute()==base.parent.parent/'bin':
        meta=json.loads((base/'install.json').read_text())
        commands=launcher_contents(Path(meta['prefix']),accounts or [],meta.get('auto',True))
        for name,content in commands.items():
            target=Path(bin_dir)/name
            if target.exists() and not own_launcher(target):raise ManagerError('Comando ajeno conservado: '+str(target))
            if not target.exists() or target.read_text()!=content:atomic_write(target,content,0o755)
        print('Atajos actualizados en '+str(bin_dir))
        return
    source = Path(source).resolve()
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True,exist_ok=True)
    commands = {"ai": []}
    selected=accounts if accounts is not None else [{'provider':p,'account':str(n)} for p,count in [('codex',2),('claude',3)] for n in range(1,count+1)]
    for account in selected:
            provider=account['provider'];prefix='x' if provider=='codex' else 'c';number=account['account']
            commands[f"{prefix}{number}"]=[provider,str(number)]
            commands[f"{prefix}{number}r"]=["resume",provider,str(number)]
            commands[f"{provider}{number}"]=[provider,str(number)]
            commands[f"{provider}{number}r"]=["resume",provider,str(number)]
    # Files are small launchers, not shell aliases; usable in existing terminals.
    import shlex
    for name,args in commands.items():
        target=bin_dir/name
        if name=="ai":
            content="#!"+sys.executable+"\nimport sys\nsys.path.insert(0, "+repr(str(source))+")\nfrom ai_manager.cli import main\nraise SystemExit(main())\n"
        else:
            content="#!/bin/sh\nexec "+shlex.quote(str(bin_dir/"ai"))+" "+" ".join(args)+' "$@"\n'
        if target.exists() or target.is_symlink():
            if not target.is_symlink() and target.read_text()==content: continue
            raise ManagerError(f"Comando preexistente: {target}; no se sobrescribe")
        atomic_write(target,content,0o755)
    print("Comandos disponibles en "+str(bin_dir)+": "+", ".join(sorted(commands)))


def install_auto_commands(source, bin_dir):
    from .providers import AUTO_WRAPPER_MARKER, executable
    source = Path(source).resolve()
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    targets = []
    for provider in ('codex', 'claude'):
        original = executable(provider)
        content = ('#!' + sys.executable + '\n' + AUTO_WRAPPER_MARKER.decode() + '\nimport sys\n'
                   'sys.path.insert(0, ' + repr(str(source)) + ')\n'
                   'from ai_manager.entrypoints import provider_main\n'
                   'raise SystemExit(provider_main(' + repr(provider) + '))\n')
        target = bin_dir / provider
        if target.exists() or target.is_symlink():
            if target.is_symlink() or target.read_text() != content:
                raise ManagerError(f'Comando preexistente: {target}; original conservado, no se sobrescribe')
        targets.append((target, content, original))
    for target, content, original in targets:
        if not target.exists():
            atomic_write(target, content, 0o755)
        print(f'Auto: {target}; original: {original}')

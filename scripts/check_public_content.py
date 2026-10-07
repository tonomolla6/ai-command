#!/usr/bin/env python3
"""Reject private files, real email examples and recognizable credentials.

This is a publishing check, not a replacement for reviewing the changes.
It reports filenames and categories only, never the matching values.
Additional local terms can be supplied in AI_COMMAND_PRIVATE_TERMS (one per line).
"""
from pathlib import Path
import argparse
import os
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent.parent
EMAIL = re.compile(rb'[A-Za-z0-9_.+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})')
EXAMPLE_DOMAINS = {b'example.org', b'example.net', b'example.com', b'example.invalid'}
CREDENTIALS = re.compile(
    rb'(?:sk-[A-Za-z0-9_\-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}'
    rb'|github_pat_[A-Za-z0-9_]{40,}'
    rb'|eyJ[A-Za-z0-9_\-]{24,}\.[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,}'
    rb'|-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----)'
)
PRIVATE_NAMES = {'auth.json', '.credentials.json', 'credentials.json', 'cookies.json',
                 'config.local.json', 'limits.json', 'id_rsa', 'id_ed25519'}
PRIVATE_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.jsonl', '.pem', '.key', '.bundle'}


def inspect(name, data, private_terms=()):
    path = Path(name)
    issues = []
    if (path.name in PRIVATE_NAMES or path.suffix in PRIVATE_SUFFIXES
            or path.name == '.env' or path.name.startswith('.env.')):
        issues.append('private-file')
    if CREDENTIALS.search(data):
        issues.append('credential-shaped-value')
    if any(m.group(1).lower() not in EXAMPLE_DOMAINS for m in EMAIL.finditer(data)):
        issues.append('non-example-email')
    if b'/' + b'root/' in data:
        issues.append('private-home-path')
    if any(term.lower() in data.lower() for term in private_terms if term):
        issues.append('local-private-term')
    return issues


def terms():
    return tuple(t.strip().encode() for t in os.environ.get('AI_COMMAND_PRIVATE_TERMS', '').splitlines() if t.strip())


def audit_files(paths, root=ROOT):
    problems = []
    for path in paths:
        name = str(path.relative_to(root))
        issues = ['symlink'] if path.is_symlink() else inspect(name, path.read_bytes(), terms())
        if issues:
            problems.append((name, issues))
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    if args.archive:
        problems = []
        with tarfile.open(args.archive, 'r:gz') as archive:
            for entry in archive:
                if entry.isdir():
                    continue
                issues = inspect(entry.name, archive.extractfile(entry).read(), terms()) if entry.isfile() else ['non-regular-file']
                if issues:
                    problems.append((entry.name, issues))
    else:
        names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).split(b'\0')
        problems = audit_files([ROOT / os.fsdecode(name) for name in names if name])
    for name, issues in problems:
        print(name + ': ' + ', '.join(issues))
    if problems:
        return 1
    print('Public content: OK (review still required)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

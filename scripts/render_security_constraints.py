#!/usr/bin/env python3
"""Apply explicit security pin replacements to an immutable upstream snapshot."""
import argparse
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    'epoxy-upper-2026-10-26.txt': 'bf8d5397f8d20d4895d6f93f9b55c5a07d686c75f944391780e95a8e43060bcb',
    'gazpacho-upper-2026-10-26.txt': '778a2873b3aee59110af8d00aa6f0b659fb98e51a86650840e1a8604719a2f37',
}


def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def render(base, overrides):
    pins = {}
    for line in overrides.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)', line)
        if not match or normalized(match[1]) in pins:
            raise ValueError('invalid_or_duplicate_security_override')
        pins[normalized(match[1])] = line
    if not pins:
        raise ValueError('empty_security_overlay')
    output, seen = [], set()
    for line in base.splitlines():
        match = re.match(r'^([A-Za-z0-9_.-]+)===?', line)
        name = normalized(match[1]) if match else None
        if name in pins:
            if name not in seen:
                output.append(pins[name])
            seen.add(name)
        else:
            output.append(line)
    if seen != set(pins):
        raise ValueError('override_package_absent_from_base')
    return '# Generated with scripts/render_security_constraints.py; not the upstream snapshot.\n' + '\n'.join(output) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--series', choices=('epoxy', 'gazpacho'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    directory = ROOT / 'deployment/constraints'
    base = directory / (args.series + '-upper-2026-10-26.txt')
    overrides = directory / 'security-overrides-2026-10-27.txt'
    if hashlib.sha256(base.read_bytes()).hexdigest() != SOURCES[base.name]:
        raise ValueError('upstream_snapshot_checksum_mismatch')
    if args.output.resolve() in (base.resolve(), overrides.resolve()):
        raise ValueError('cannot_overwrite_source_constraints')
    content = render(base.read_text(), overrides.read_text())
    args.output.write_text(content)


if __name__ == '__main__':
    main()

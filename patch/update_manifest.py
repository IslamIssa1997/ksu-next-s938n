#!/usr/bin/env python3
"""Update targets-v3.json with built ksud binary URL and size."""

import json
import os
import sys


def main():
    if len(sys.argv) < 3:
        print("Usage: update_manifest.py <targets-v3.json> <ksud-size-bytes>", file=sys.stderr)
        sys.exit(1)

    manifest_path = sys.argv[1]
    ksud_size = int(sys.argv[2])

    repo = os.environ.get('GITHUB_REPOSITORY', '')
    if not repo:
        print("GITHUB_REPOSITORY not set", file=sys.stderr)
        sys.exit(1)

    ksud_url = f"https://raw.githubusercontent.com/{repo}/main/kernelsu/ksud-s938n-next"

    with open(manifest_path, 'r') as f:
        manifest = json.load(f)

    for target in manifest.get('targets', []):
        if target.get('payloadId') == 'galaxy-s25-series-2026-06-07':
            target['kernelsu']['url'] = ksud_url
            target['kernelsu']['size'] = ksud_size
            print(f"Updated kernelsu url={ksud_url} size={ksud_size}")

    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()

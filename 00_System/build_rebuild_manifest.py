"""Build the parallel, read-only rebuild-v1 source manifest."""
from __future__ import annotations

import argparse
from pathlib import Path

from vault_rebuild import build_manifest, write_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    output = args.output or args.vault / "00_System" / "Data" / "rebuild-v1" / "source-manifest.json"
    manifest = build_manifest(args.vault)
    write_manifest(manifest, output)
    validation = manifest["validation"]
    print(f"records={len(manifest['records'])} readable={validation['readable_count']} unreadable={validation['unreadable_count']} "
          f"duplicate_ids={len(validation['duplicate_stable_ids'])} duplicate_hashes={len(validation['duplicate_content_hashes'])} output={output}")
    return 0 if not validation["unreadable_count"] and not validation["duplicate_stable_ids"] and not validation["duplicate_content_hashes"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

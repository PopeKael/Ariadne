#!/usr/bin/env python3
"""CLI entrypoint for the Ariadne local embedding index."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from ariadne_embeddings import DEFAULT_MODEL, build_index, index_path, load_index
from ariadne_mcp import ROOT, chunk_records

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    if args.status:
        index = load_index(ROOT)
        if not index:
            print(json.dumps({"status": "missing", "path": str(index_path(ROOT))}, indent=2)); return 0
        print(json.dumps({"status": "ready", "path": str(index_path(ROOT)), "model": index.get("model"),
                          "dimensions": index.get("dimensions"), "chunks": len(index.get("entries", {})),
                          "failures": len(index.get("failures", {})), "storage_bytes": index_path(ROOT).stat().st_size}, indent=2)); return 0
    report = build_index(ROOT, list(chunk_records()), rebuild=args.rebuild, model=args.model)
    print(json.dumps(report, indent=2))
    return 0
if __name__ == "__main__": raise SystemExit(main())

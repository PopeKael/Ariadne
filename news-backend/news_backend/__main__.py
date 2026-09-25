from __future__ import annotations

import argparse
import json
import time
import urllib.request
from unittest.mock import patch

from .service import NewsStore, collect_once, serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="ariadne-news-backend")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    collect = sub.add_parser("collect-once")
    collect.add_argument("--force", action="store_true", help="ignore saved feed validators for a deterministic cycle")
    curate = sub.add_parser("curate-once", help="rebuild the persisted Top 100 from local cache only")
    verify = sub.add_parser("curate-verify", help="repeat curation and prove stable output with no URL fetch")
    verify.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.command == "serve":
        serve()
        return
    store = NewsStore()
    try:
        import news_backend.service as service
        if args.command == "collect-once":
            service._CYCLE_START = time.perf_counter()
            result = collect_once(store, force=args.force)
        elif args.command == "curate-once":
            result = store.curate_top100()
        else:
            runs = max(2, min(int(args.runs), 50))
            elapsed: list[float] = []
            reports: list[dict] = []
            fetch_calls = 0

            def forbidden_fetch(*_args, **_kwargs):
                nonlocal fetch_calls
                fetch_calls += 1
                raise AssertionError("publisher/network fetch attempted during curation")

            with patch.object(urllib.request, "urlopen", side_effect=forbidden_fetch):
                for _ in range(runs):
                    started = time.perf_counter()
                    reports.append(store.curate_top100())
                    elapsed.append(round((time.perf_counter() - started) * 1000, 3))
            fingerprints = {(item["input_hash"], item["result_hash"], json.dumps(item["articles"], sort_keys=True))
                            for item in reports}
            if len(fingerprints) != 1 or fetch_calls:
                raise SystemExit("FAIL: curation output changed or attempted publisher/network access")
            result = {
                "ok": True, "runs": runs, "stable": True, "publisher_fetches": fetch_calls,
                "article_count": reports[-1]["article_count"], "candidate_count": reports[-1]["candidate_count"],
                "input_hash": reports[-1]["input_hash"], "result_hash": reports[-1]["result_hash"],
                "run_count": reports[-1]["run_count"], "elapsed_ms_each": elapsed,
                "average_elapsed_ms": round(sum(elapsed) / len(elapsed), 3),
            }
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    main()

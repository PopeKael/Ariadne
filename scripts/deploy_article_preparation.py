"""Run on Hera after staging the tested image-preparation bundle.

Deploy through each service's authoritative Compose project.
Rollback uses archived definitions and SQLite backups, not duplicate containers.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request

DOCKER = "/usr/local/bin/docker"
BUILD = "20261004-article-preparation-1"
ROOT = Path(__file__).resolve().parent
SERVICES = [
    ("ariadne-article-cache-spike", "article-cache", "http://127.0.0.1:8790/v1/cache/health", {"ARTICLE_CACHE_PUBLIC_URL": "http://192.168.1.200:8790"}),
    ("ariadne-signal-service", "signal", "http://127.0.0.1:8788/v1/health", {}),
    ("ariadne-news-backend", "news", "http://127.0.0.1:8791/health", {"NEWS_BACKEND_ARTICLE_CACHE_URL": "http://192.168.1.200:8790"}),
    ("ariadne-discovery-service", "discovery", "http://127.0.0.1:8789/v1/health", {"DISCOVERY_SERVICE_ARTICLE_CACHE_URL": "http://192.168.1.200:8790", "DISCOVERY_SERVICE_ARTICLE_CACHE_TIMEOUT_SECONDS": "60"}),
]


def run(*args):
    prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
    return subprocess.check_output(prefix + [DOCKER] + list(args), text=True).strip()


def healthy(url):
    for _ in range(60):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.load(response)
                if payload.get("ok") and payload.get("build_sha") == BUILD:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main():
    os.umask(0o077)
    backup = ROOT / ("before-" + time.strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(mode=0o700)
    states, plans, originals = {}, {}, {}
    for name, context, url, extra in SERVICES:
        state = json.loads(run("inspect", name))[0]
        labels = state["Config"].get("Labels") or {}
        project = labels.get("com.docker.compose.project")
        filename = labels.get("com.docker.compose.project.config_files")
        service = labels.get("com.docker.compose.service")
        if not project or not service or not filename or "," in filename:
            raise RuntimeError(name + " must have one authoritative Compose project before deployment")
        # Reconciled KStore definitions are JSON, a valid Compose YAML representation.
        source = Path(filename)
        document = json.loads(source.read_text())
        originals[name] = (source, source.read_text(), project)
        states[name] = state
        (backup / (name + ".json")).write_text(json.dumps(state, indent=2))
        (backup / (name + "-compose.json")).write_text(source.read_text())
        spec = document["services"][service]
        environment = spec.get("environment") or {}
        if isinstance(environment, list):
            environment = dict(value.split("=", 1) for value in environment if "=" in value)
        environment.update(extra, ARIADNE_BUILD_SHA=BUILD)
        spec["environment"] = environment
        spec["image"] = "ariadne-%s:%s" % (context, BUILD)
        plans[name] = document
    # Build all images before stopping any service.
    for name, context, url, extra in SERVICES:
        dockerfile = "article-cache.Dockerfile" if context == "article-cache" else "Dockerfile"
        print("BUILD " + context, flush=True)
        prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
        subprocess.check_call(prefix + [DOCKER, "build", "-f", str(ROOT / context / dockerfile), "-t", "ariadne-%s:%s" % (context, BUILD), str(ROOT / context)])
    completed = []
    try:
        for name, context, url, extra in SERVICES:
            print("UPDATE " + name, flush=True)
            completed.append(name)
            run("stop", name)
            # Back up SQLite with its own online backup API after the owner stops.
            for mount in states[name].get("Mounts", []):
                if mount["RW"]:
                    for database in Path(mount["Source"]).glob("*.sqlite3"):
                        source = sqlite3.connect(str(database))
                        target = sqlite3.connect(str(backup / (name + "-" + database.name)))
                        source.backup(target)
                        target.close()
                        source.close()
            source, original, project = originals[name]
            source.write_text(json.dumps(plans[name], indent=2))
            run("compose", "-p", project, "-f", str(source), "up", "-d")
            if not healthy(url):
                raise RuntimeError(name + " failed its health check")
    except Exception:
        for name in reversed(completed):
            source, original, project = originals[name]
            source.write_text(original)
            run("compose", "-p", project, "-f", str(source), "up", "-d")
        raise
    print("DEPLOYED " + BUILD + "; rollback definitions and SQLite backups at " + str(backup), flush=True)
    print("Validate three known articles before running selective backfill.", flush=True)


if __name__ == "__main__":
    main()

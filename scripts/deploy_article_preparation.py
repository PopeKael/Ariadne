"""Run on Hera with sudo after staging the tested image-preparation bundle.

Keep original containers stopped under backup names for direct rollback.
Databases and image files retain their original bind mounts.
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
    return subprocess.check_output([DOCKER] + list(args), text=True).strip()


def create_arguments(state, image, overrides):
    config, host = state["Config"], state["HostConfig"]
    if host.get("Privileged") or host.get("Devices") or host.get("NetworkMode") == "host":
        raise RuntimeError("Unexpected container privileges or host networking; inspect before deploying")
    args = ["create", "--name", state["Name"].lstrip("/"), "--network", host.get("NetworkMode") or "bridge"]
    restart = host.get("RestartPolicy", {}).get("Name")
    if restart:
        args += ["--restart", restart]
    for mount in state.get("Mounts", []):
        if mount["Type"] != "bind":
            raise RuntimeError("Unexpected non-bind mount; refusing to change storage")
        value = "type=bind,src=%s,dst=%s" % (mount["Source"], mount["Destination"])
        if not mount["RW"]:
            value += ",readonly"
        args += ["--mount", value]
    for port, bindings in (host.get("PortBindings") or {}).items():
        for binding in bindings or []:
            address = binding.get("HostIp") or "0.0.0.0"
            args += ["--publish", "%s:%s:%s" % (address, binding["HostPort"], port)]
    environment = dict(value.split("=", 1) for value in config.get("Env", []) if "=" in value)
    environment.update(overrides)
    for key, value in environment.items():
        args += ["--env", key + "=" + value]
    for key, value in (config.get("Labels") or {}).items():
        args += ["--label", key + "=" + value]
    for extra in host.get("ExtraHosts") or []:
        args += ["--add-host", extra]
    for value in host.get("Dns") or []:
        args += ["--dns", value]
    if config.get("User"):
        args += ["--user", config["User"]]
    if config.get("WorkingDir"):
        args += ["--workdir", config["WorkingDir"]]
    if host.get("ReadonlyRootfs"):
        args += ["--read-only"]
    # These Ariadne services use the image's own entrypoint and CMD.
    args += [image]
    return args


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
    if os.geteuid() != 0:
        raise SystemExit("Run with sudo; no database or container changes made.")
    backup = ROOT / ("before-" + time.strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(mode=0o700)
    states, plans = {}, {}
    for name, context, url, extra in SERVICES:
        state = json.loads(run("inspect", name))[0]
        states[name] = state
        (backup / (name + ".json")).write_text(json.dumps(state, indent=2))
        overrides = dict(extra, ARIADNE_BUILD_SHA=BUILD)
        plans[name] = create_arguments(state, "ariadne-%s:%s" % (context, BUILD), overrides)
    # Build all images before stopping any service.
    for name, context, url, extra in SERVICES:
        dockerfile = "article-cache.Dockerfile" if context == "article-cache" else "Dockerfile"
        print("BUILD " + context, flush=True)
        subprocess.check_call([DOCKER, "build", "-f", str(ROOT / context / dockerfile), "-t", "ariadne-%s:%s" % (context, BUILD), str(ROOT / context)])
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
            run("rename", name, name + "-before-" + BUILD)
            run(*plans[name])
            run("start", name)
            if not healthy(url):
                raise RuntimeError(name + " failed its health check")
    except Exception:
        for name in reversed(completed):
            try:
                run("inspect", name + "-before-" + BUILD)
            except subprocess.CalledProcessError:
                run("start", name)
                continue
            try:
                run("stop", name)
                run("rename", name, name + "-failed-" + BUILD)
            except subprocess.CalledProcessError:
                pass
            run("rename", name + "-before-" + BUILD, name)
            run("start", name)
        raise
    # Preserve staged source as the deployment source of truth; rollback containers
    # keep their original immutable images. No full database rebuild is triggered.
    print("DEPLOYED " + BUILD + "; original containers and SQLite backups retained at " + str(backup), flush=True)
    print("Validate three known articles before running selective backfill.", flush=True)


if __name__ == "__main__":
    main()

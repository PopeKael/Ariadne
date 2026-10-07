"""Snapshot NAS Docker state before project reconciliation; no lifecycle changes.

Run as the authorized SSH owner on KStore. Full inspect records stay in a private archive;
inventory.json contains no environment values and is readable by the SSH owner.
"""
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess

DOCKER = "/usr/local/bin/docker"
COMPOSE_FILES = [
    "/volume1/docker/searxng-compose/compose.yaml",
    "/volume1/docker/n8n-compose/compose.yaml",
    "/volume1/web/site/garage/compose.yaml",
    "/volume1/web/site/salon/compose.yaml",
    "/volume1/web/site/linktree/compose.yaml",
    "/volume1/docker/ariadne-signal-service/docker-compose.yml",
    "/volume1/docker/ariadne-discovery-service/discovery-service/docker-compose.yml",
]


def docker(*args):
    prefix = [] if os.geteuid() == 0 else ["/usr/bin/sudo", "-n"]
    return subprocess.check_output([*prefix, DOCKER, *args], text=True).strip()


def summarize(state):
    labels = state.get("Config", {}).get("Labels") or {}
    return {
        "name": state["Name"].lstrip("/"), "id": state["Id"],
        "image": state["Config"].get("Image"), "image_id": state.get("Image"),
        "status": state["State"].get("Status"),
        "running": state["State"].get("Running"),
        "error": state["State"].get("Error"),
        "project": labels.get("com.docker.compose.project"),
        "service": labels.get("com.docker.compose.service"),
        "compose_files": labels.get("com.docker.compose.project.config_files"),
        "restart_policy": state["HostConfig"].get("RestartPolicy"),
        "ports": state["HostConfig"].get("PortBindings"),
        "mounts": [{key: mount.get(key) for key in ("Type", "Source", "Destination", "RW", "Name")} for mount in state.get("Mounts", [])],
        "networks": sorted(state.get("NetworkSettings", {}).get("Networks", {})),
    }


def main():
    os.umask(0o077)
    root = Path(__file__).resolve().parent
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = root / ("private-before-" + stamp)
    archive.mkdir(mode=0o700)
    ids = docker("ps", "-aq").splitlines()
    states = json.loads(docker("inspect", *ids)) if ids else []
    inventory = {"captured_at": stamp, "containers": [summarize(state) for state in states],
                 "images": [json.loads(line) for line in docker("image", "ls", "--no-trunc", "--format", "{{json .}}").splitlines()],
                 "compose_projects": docker("compose", "ls", "--all", "--format", "json"),
                 "private_archive": str(archive)}
    (archive / "containers.json").write_text(json.dumps(states, indent=2))
    for index, filename in enumerate(COMPOSE_FILES):
        path = Path(filename)
        if path.is_file():
            shutil.copy2(path, archive / (str(index) + "-" + path.name))
    report = root / "inventory.json"
    report.write_text(json.dumps(inventory, indent=2))
    report.chmod(0o644)
    print(json.dumps({"inventory": str(report), "private_archive": str(archive),
                      "containers": len(states), "lifecycle_changes": False}))


if __name__ == "__main__":
    main()

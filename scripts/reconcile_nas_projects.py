"""One-time KStore project reconciliation, preserving inspected runtime and storage.

Run as Wazza after the private audit snapshot. Definitions with environment values
stay on the NAS. Compose owns replacements; no renamed rollback containers remain.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request

ROOT = Path('/volume1/docker/ariadne-maintenance-20261007')
DOCKER = ['sudo', '-n', '/usr/local/bin/docker']
SERVICES = [
    ('ariadne-signal-service', 'ariadne-signal-service', 'ariadne-signal-service', '/volume1/docker/ariadne-signal-service/docker-compose.yml', 8788, '/v1/health'),
    ('ariadne-discovery-service', 'ariadne-discovery-service', 'ariadne-discovery-service', '/volume1/docker/ariadne-discovery-service/discovery-service/docker-compose.yml', 8789, '/v1/health'),
    ('ariadne-article-cache-spike', 'ariadne-article-cache', 'article-cache', '/volume1/docker/ariadne-article-cache-spike/compose.yaml', 8790, '/v1/cache/health'),
    ('ariadne-news-backend', 'ariadne-news-backend', 'news', '/volume1/docker/ariadne-news-backend/compose.yaml', 8791, '/health'),
]


def run(*args):
    return subprocess.check_output(DOCKER + list(args), text=True).strip()


def definition(state, service):
    config, host = state['Config'], state['HostConfig']
    if host.get('Privileged') or host.get('Devices') or host.get('NetworkMode') == 'host':
        raise RuntimeError('Unexpected privileges/network mode')
    result = {'image': state['Image'], 'container_name': state['Name'].lstrip('/'),
              'restart': host['RestartPolicy']['Name'] or 'no',
              'environment': config.get('Env', []), 'volumes': [], 'ports': []}
    for mount in state['Mounts']:
        if mount['Type'] != 'bind':
            raise RuntimeError('Expected existing bind storage')
        result['volumes'].append({'type': 'bind', 'source': mount['Source'],
                                 'target': mount['Destination'], 'read_only': not mount['RW']})
    for port, bindings in (host.get('PortBindings') or {}).items():
        for binding in bindings or []:
            number, protocol = port.split('/')
            result['ports'].append({'target': int(number), 'published': binding['HostPort'],
                                    'protocol': protocol, 'host_ip': binding['HostIp'] or '0.0.0.0'})
    for source, target in [('Cmd', 'command'), ('Entrypoint', 'entrypoint'), ('User', 'user'), ('WorkingDir', 'working_dir'), ('Healthcheck', 'healthcheck')]:
        if config.get(source):
            if source == 'Healthcheck':
                raise RuntimeError('Review custom healthcheck before conversion')
            result[target] = config[source]
    if host.get('ExtraHosts'): result['extra_hosts'] = host['ExtraHosts']
    if host.get('Dns'): result['dns'] = host['Dns']
    if host.get('ReadonlyRootfs'): result['read_only'] = True
    document = {'name': service, 'services': {service: result}}
    network = host.get('NetworkMode') or 'bridge'
    if network == 'bridge':
        result['network_mode'] = 'bridge'
    else:
        # Preserve network identity: NAS reverse-proxy access rules can depend on it.
        result['networks'] = ['existing']
        document['networks'] = {'existing': {'external': True, 'name': network}}
    return document


def healthy(port, path, attempts=30):
    for _ in range(attempts):
        try:
            with urllib.request.urlopen('http://127.0.0.1:%s%s' % (port, path), timeout=3) as response:
                if response.status == 200: return True
        except Exception: pass
        time.sleep(1)
    return False


def main():
    os.umask(0o077)
    archive = ROOT / ('private-reconcile-' + time.strftime('%Y%m%d-%H%M%S'))
    archive.mkdir(mode=0o700)
    for name, project, service, filename, port, path in SERVICES:
        state = json.loads(run('inspect', name))[0]
        labels = state['Config'].get('Labels') or {}
        if (labels.get('com.docker.compose.project') == project
                and labels.get('com.docker.compose.project.config_files') == filename
                and state['Config']['Image'].startswith('sha256:')
                and healthy(port, path)):
            print('ALREADY HEALTHY ' + name + ' project=' + project, flush=True)
            continue
        (archive / (name + '.json')).write_text(json.dumps(state, indent=2))
        document = definition(state, service)
        document['name'] = project
        target = Path(filename)
        if target.exists(): (archive / (name + '-compose-before.json')).write_bytes(target.read_bytes())
        candidate = archive / (name + '-candidate.json')
        candidate.write_text(json.dumps(document, indent=2))
        run('compose', '-p', project, '-f', str(candidate), 'config', '--quiet')
        print('RECONCILE ' + name, flush=True)
        run('stop', '-t', '30', name)
        # Source backup uses SQLite's backup API, never a raw live database copy.
        for mount in state['Mounts']:
            if mount['RW']:
                for database in Path(mount['Source']).glob('*.sqlite3'):
                    with sqlite3.connect(str(database)) as source, sqlite3.connect(str(archive / (name + '-' + database.name))) as dest:
                        source.backup(dest)
        target.write_text(candidate.read_text())
        target.chmod(0o600)
        run('rm', name)
        try:
            run('compose', '-p', project, '-f', filename, 'up', '-d')
            if not healthy(port, path): raise RuntimeError('Health failed: ' + name)
        except Exception:
            # Same immutable image/runtime definition, retry project-managed recovery.
            run('compose', '-p', project, '-f', filename, 'up', '-d')
            raise
        print('HEALTHY ' + name + ' project=' + project, flush=True)
    print('RECONCILED archive=' + str(archive), flush=True)


if __name__ == '__main__':
    main()

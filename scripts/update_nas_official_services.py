"""KStore maintenance rollout with data backups and exact image rollback."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from reconcile_nas_projects import definition, run, healthy, ROOT, DOCKER


def main():
    os.umask(0o077)
    reuse = os.environ.get('ARIADNE_UPDATE_ARCHIVE')
    archive = Path(reuse) if reuse else ROOT / ('private-updates-' + time.strftime('%Y%m%d-%H%M%S'))
    if reuse:
        if archive.parent != ROOT or not (archive / 'previous-images.tar').is_file():
            raise RuntimeError('Verified previous image archive required')
    else:
        archive.mkdir(mode=0o700)
    services = [
        ('garage', 'garage', 'garage', '/volume1/web/site/garage/compose.yaml', 'httpd:latest', 8085),
        ('linktree', 'linktree', 'linktree', '/volume1/web/site/linktree/compose.yaml', 'httpd:latest', 8084),
        ('salon-salon-1', 'salon', 'salon', '/volume1/web/site/salon/compose.yaml', 'httpd:latest', 8081),
        ('n8n-n8n-1', 'n8n', 'n8n', '/volume1/docker/n8n-compose/compose.yaml', 'n8nio/n8n:latest', 5678),
        ('searxng-searxng_project-1', 'searxng', 'searxng_project', '/volume1/docker/searxng-compose/compose.yaml', 'searxng/searxng:latest', 8082),
    ]
    states = {name: json.loads(run('inspect', name))[0] for name, *_ in services}
    # Archive images before pruning; previous immutable versions remain loadable.
    old_images = sorted({state['Image'] for state in states.values()})
    print('ARCHIVE previous official images', flush=True)
    if not reuse:
        run('image', 'save', '-o', str(archive / 'previous-images.tar'), *old_images)
    for name, project, service, filename, image, port in services:
        state = states[name]
        wanted = json.loads(run('image', 'inspect', image))[0]['Id']
        if state['Image'] == wanted and project != 'n8n' and healthy(port, '/'):
            print('ALREADY HEALTHY ' + project, flush=True)
            continue
        if name.startswith('searxng'):
            # Convert anonymous volumes to explicit external references, preserving contents.
            bindings = state['Mounts']
            state['Mounts'] = []
            document = definition(state, service)
            document['volumes'] = {}
            for i, mount in enumerate(bindings):
                key = 'existing_' + str(i)
                document['volumes'][key] = {'external': True, 'name': mount['Name']}
                document['services'][service]['volumes'].append({'type': 'volume', 'source': key, 'target': mount['Destination'], 'read_only': not mount['RW']})
            state['Mounts'] = bindings
        else:
            document = definition(state, service)
        document['name'] = project
        original = json.loads(json.dumps(document))
        spec = document['services'][service]
        spec['image'] = wanted
        spec['restart'] = 'unless-stopped'
        if project == 'n8n':
            # Preserve the formerly separate URL using a second port on ONE process.
            extra_port = {'target': 5678, 'published': '32770', 'protocol': 'tcp', 'host_ip': '0.0.0.0'}
            if not any(p['published'] == '32770' for p in spec['ports']):
                spec['ports'].append(extra_port)
                original['services'][service]['ports'].append(extra_port)
            run('stop', '-t', '30', 'N8N-Automation')
        target = Path(filename)
        if target.exists(): (archive / (project + '-file-before.json')).write_bytes(target.read_bytes())
        (archive / (project + '-rollback.json')).write_text(json.dumps(original, indent=2))
        (archive / (project + '-inspect.json')).write_text(json.dumps(state, indent=2))
        candidate = archive / (project + '-candidate.json')
        candidate.write_text(json.dumps(document, indent=2))
        run('compose', '-p', project, '-f', str(candidate), 'config', '--quiet')
        print('UPDATE ' + project, flush=True)
        run('stop', '-t', '30', name)
        if project == 'n8n':
            data = Path('/volume1/docker/n8n/data')
            # Preserve node-owned 0600 encryption config via privileged Docker copy.
            if not (archive / 'n8n-data').exists():
                run('cp', '-a', name + ':/home/node/.n8n', str(archive / 'n8n-data'))
            with sqlite3.connect(str(data / 'database.sqlite')) as db:
                count = db.execute('select count(*) from workflow_entity').fetchone()[0]
            (archive / 'n8n-workflow-count.txt').write_text(str(count))
            print('N8N_BACKUP workflows=' + str(count), flush=True)
        target.write_text(json.dumps(document, indent=2))
        if target.stat().st_uid == os.geteuid():
            target.chmod(0o600)
        try:
            run('compose', '-p', project, '-f', filename, 'up', '-d', '--force-recreate')
            path = '/healthz' if project == 'n8n' else '/'
            if not healthy(port, path, attempts=180 if project == 'n8n' else 30):
                raise RuntimeError(project + ' HTTP check failed')
        except Exception:
            run('stop', '-t', '30', name)
            if project == 'n8n':
                run('cp', '-a', str(archive / 'n8n-data') + '/.', name + ':/home/node/.n8n')
            target.write_text(json.dumps(original, indent=2))
            run('compose', '-p', project, '-f', filename, 'up', '-d')
            raise
        if project == 'n8n':
            run('rm', 'N8N-Automation')
            with sqlite3.connect('/volume1/docker/n8n/data/database.sqlite') as db:
                after = db.execute('select count(*) from workflow_entity').fetchone()[0]
            if after != count: raise RuntimeError('Workflow count changed')
            print('N8N_VERIFIED workflows=' + str(after) + ' version=' + run('exec', name, 'n8n', '--version'), flush=True)
        print('HEALTHY ' + project, flush=True)
    # Retire the unmounted temporary web shell after archiving its writable state.
    print('ARCHIVE temporary ttyd shell', flush=True)
    run('stop', '-t', '15', 'tsl0922-ttyd-1')
    run('export', '-o', str(archive / 'temporary-ttyd-filesystem.tar'), 'tsl0922-ttyd-1')
    run('rm', 'tsl0922-ttyd-1')
    print('UPDATED archive=' + str(archive), flush=True)


if __name__ == '__main__':
    main()

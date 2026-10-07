"""Verify the authorized KStore inventory before pruning unused images only."""
import json
import subprocess
import urllib.request
from pathlib import Path
from reconcile_nas_projects import ROOT, run

EXPECTED = {'garage', 'salon', 'linktree', 'n8n', 'searxng',
            'ariadne-signal-service', 'ariadne-discovery-service',
            'ariadne-article-cache', 'ariadne-news-backend'}


def get(port, path='/'):
    with urllib.request.urlopen('http://127.0.0.1:%s%s' % (port, path), timeout=25) as response:
        raw = response.read()
        return response.status, raw


def main():
    states = json.loads(run('inspect', *run('ps', '-aq').splitlines()))
    projects = [c['Config']['Labels'].get('com.docker.compose.project') for c in states]
    if len(states) != 9 or set(projects) != EXPECTED or not all(c['State']['Running'] for c in states):
        raise RuntimeError('Expected exactly nine running, project-owned containers')
    report = {'containers': [{'name': c['Name'].lstrip('/'), 'project': projects[i], 'image_id': c['Image'], 'running': c['State']['Running']} for i, c in enumerate(states)], 'http': {}}
    for port, path in [(8081, '/'), (8084, '/'), (8085, '/'), (8082, '/'), (5678, '/healthz'), (32770, '/healthz'), (8788, '/v1/health'), (8789, '/v1/health'), (8790, '/v1/cache/health'), (8791, '/health')]:
        status, body = get(port, path)
        report['http'][str(port)] = status
        print('HTTP', port, status, flush=True)
    status, body = get(8082, '/search?q=Thailand+news&format=json')
    result = json.loads(body)
    report['search_results'] = len(result.get('results', []))
    report['search_engine_errors'] = result.get('unresponsive_engines', [])
    if not report['search_results']: raise RuntimeError('SearXNG returned no test search results')
    print('SEARXNG_SEARCH results=' + str(report['search_results']), flush=True)
    report['n8n_version'] = run('exec', 'n8n-n8n-1', 'n8n', '--version')
    report['httpd_version'] = run('exec', 'garage', 'httpd', '-v')
    report['before_images'] = len(set(run('image', 'ls', '-q').splitlines()))
    # Docker refuses to prune images referenced by ANY remaining container.
    print('PRUNE unused images; volumes preserved', flush=True)
    (ROOT / 'image-prune.log').write_text(run('image', 'prune', '-a', '-f'))
    report['after_images'] = len(set(run('image', 'ls', '-q').splitlines()))
    report['compose_projects'] = json.loads(run('compose', 'ls', '--all', '--format', 'json'))
    (ROOT / 'verified-result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()

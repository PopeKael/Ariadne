"""Keep Hera's existing build contexts aligned with the reviewed deployment.

Runs as Wazza. Copies code only; does not restart containers or touch data.
"""
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parent
BUILD = "20261004-article-preparation-1"
CONTEXTS = {
    "article-cache": Path('/volume1/docker/ariadne-article-cache-spike/app'),
    "discovery": Path('/volume1/docker/ariadne-discovery-service/discovery-service'),
    "signal": Path('/volume1/docker/ariadne-signal-service'),
    "news": Path('/volume1/docker/ariadne-news-backend/app'),
}
ITEMS = {
    "article-cache": ['discovery_service', 'article-cache.Dockerfile', 'cache_run.py', 'requirements.txt'],
    "discovery": ['discovery_service', 'Dockerfile', 'run.py'],
    "signal": ['signal_service', 'Dockerfile', 'run.py'],
    "news": ['news_backend', 'discovery_service', 'Dockerfile', 'requirements.txt'],
}


def main():
    backup = ROOT / 'source-before-preparation.tar.gz'
    if not backup.exists():
        with tarfile.open(str(backup), 'w:gz') as archive:
            for context, destination in CONTEXTS.items():
                for name in ITEMS[context] + ['docker-compose.yml']:
                    path = destination / name
                    if path.exists():
                        archive.add(str(path), arcname=context + '/' + name)
    for context, destination in CONTEXTS.items():
        for name in ITEMS[context]:
            source = ROOT / context / name
            target = destination / name
            if source.is_dir():
                # Write file contents without copying Windows read-only mode bits.
                for path in source.rglob('*'):
                    if path.is_file() and '__pycache__' not in path.parts:
                        output = target / path.relative_to(source)
                        output.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(str(path), str(output))
            else:
                shutil.copyfile(str(source), str(target))
        print('SOURCE_SYNC ' + context)
    compose = CONTEXTS['signal'] / 'docker-compose.yml'
    text = compose.read_text()
    text = text.replace('ARIADNE_RUNTIME_BUILD: 20261002-private-https-4', 'ARIADNE_RUNTIME_BUILD: ' + BUILD)
    text = text.replace('image: ariadne-signal-service:20261002-private-https-4', 'image: ariadne-signal:' + BUILD)
    compose.write_text(text)
    compose = CONTEXTS['discovery'] / 'docker-compose.yml'
    text = compose.read_text()
    additions = {'DISCOVERY_SERVICE_ARTICLE_CACHE_URL': 'http://192.168.1.200:8790', 'DISCOVERY_SERVICE_ARTICLE_CACHE_TIMEOUT_SECONDS': '60', 'ARIADNE_BUILD_SHA': BUILD}
    for key, value in additions.items():
        if key + ':' not in text:
            text = text.replace('    environment:\n', '    environment:\n      ' + key + ': ' + value + '\n', 1)
    compose.write_text(text)
    print('SOURCE_SYNC_COMPLETE; original sources retained at ' + str(backup))


if __name__ == '__main__':
    main()

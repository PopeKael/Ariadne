"""Deploy the cache MIME fix only, then resume the verified selective backfill."""
import subprocess
import deploy_article_preparation as deployment

deployment.BUILD = '20261004-article-preparation-2'
deployment.SERVICES = deployment.SERVICES[:1]

if __name__ == '__main__':
    deployment.main()
    subprocess.check_call(['sh', str(deployment.ROOT / 'finish_article_preparation.sh')])

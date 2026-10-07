import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import deploy_article_preparation as deployment
from reconcile_nas_projects import definition


class ComposeDeploymentTests(unittest.TestCase):
    def test_reconciliation_preserves_original_proxy_network_and_storage(self):
        state = {'Name': '/signal', 'Image': 'sha256:original',
                 'Config': {'Env': ['SETTING=kept']},
                 'HostConfig': {'NetworkMode': 'signal_default', 'RestartPolicy': {'Name': 'unless-stopped'},
                                'PortBindings': {'8788/tcp': [{'HostPort': '8788', 'HostIp': ''}]}},
                 'Mounts': [{'Type': 'bind', 'Source': '/original/data', 'Destination': '/data', 'RW': True}]}
        result = definition(state, 'signal')
        self.assertEqual(result['networks']['existing'], {'external': True, 'name': 'signal_default'})
        self.assertEqual(result['services']['signal']['networks'], ['existing'])
        self.assertNotIn('network_mode', result['services']['signal'])
        self.assertEqual(result['services']['signal']['volumes'][0]['source'], '/original/data')
        self.assertEqual(result['services']['signal']['environment'], ['SETTING=kept'])

    def exercise(self, succeeds):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'compose.yaml'
            original = json.dumps({'services': {'signal': {'image': 'old-image', 'environment': ['PRIOR=kept'], 'volumes': ['/data:/data']}}})
            source.write_text(original)
            state = {'Config': {'Labels': {'com.docker.compose.project': 'signal-project', 'com.docker.compose.service': 'signal', 'com.docker.compose.project.config_files': str(source)}}, 'Mounts': []}
            commands = []

            def run(*args):
                commands.append(args)
                return json.dumps([state]) if args[0] == 'inspect' else ''

            with patch.object(deployment, 'ROOT', root), patch.object(deployment, 'SERVICES', [('signal', 'signal', 'health', {'NEW': 'value'})]), patch.object(deployment, 'run', side_effect=run), patch.object(deployment, 'healthy', return_value=succeeds), patch.object(deployment.os, 'geteuid', return_value=0, create=True), patch.object(deployment.subprocess, 'check_call'):
                if succeeds:
                    deployment.main()
                    result = json.loads(source.read_text())['services']['signal']
                    self.assertEqual(result['environment']['PRIOR'], 'kept')
                    self.assertEqual(result['environment']['NEW'], 'value')
                    self.assertEqual(result['volumes'], ['/data:/data'])
                else:
                    with self.assertRaisesRegex(RuntimeError, 'health check'):
                        deployment.main()
                    self.assertEqual(source.read_text(), original)
            self.assertTrue(any(args[:4] == ('compose', '-p', 'signal-project', '-f') for args in commands))
            self.assertFalse(any(args[0] in ('rename', 'create', 'rm') for args in commands))
            return commands

    def test_success_uses_authoritative_project_without_backup_containers(self):
        self.exercise(True)

    def test_failure_restores_definition_through_same_project(self):
        commands = self.exercise(False)
        self.assertEqual(sum(args[0] == 'compose' for args in commands), 2)


if __name__ == '__main__':
    unittest.main()

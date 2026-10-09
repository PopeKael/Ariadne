import unittest
from unittest.mock import patch
import lab_runner


class LabRunnerTests(unittest.TestCase):
    def setUp(self):
        lab_runner._JOBS.clear()
        self.saved = patch.object(lab_runner.code_lab, "saved_result", return_value={"ok": True, "run": {}, "code": "<html></html>"})
        self.saved.start(); self.addCleanup(self.saved.stop)
        self.emit = patch.object(lab_runner, "emit", return_value=True)
        self.sender = self.emit.start(); self.addCleanup(self.emit.stop)

    def test_launch_only_requests_host_and_waits_for_actual_readiness(self):
        job = lab_runner.launch('a' * 32)
        self.assertEqual(job['state'], 'REQUESTED')
        self.sender.assert_called_once_with('launch_lab_runner', run_id='a' * 32, launch_id=job['launch_id'])
        lab_runner.report({'launch_id': job['launch_id'], 'state': 'READY', 'viewport': {'width': 1920, 'height': 1080}})
        lab_runner.report({'launch_id': job['launch_id'], 'state': 'LAUNCHED'})
        self.assertEqual(lab_runner.status(job['launch_id'])['state'], 'READY')
        self.assertEqual(lab_runner.status(job['launch_id'])['viewport']['height'], 1080)

    def test_launch_rejects_missing_paths_urls_and_shell_arguments(self):
        for run_id in ['', '../index.html', 'https://example.com', '--kiosk', 'A' * 32]:
            with self.subTest(run_id=run_id), self.assertRaises(ValueError):
                lab_runner.launch(run_id)
        self.sender.assert_not_called()

    def test_focus_acknowledgement_preserves_build_state_and_viewport(self):
        job = lab_runner.launch('a' * 32)
        lab_runner.report({'launch_id': job['launch_id'], 'state': 'READY', 'viewport': {'width': 1920}})
        lab_runner.report({'launch_id': job['launch_id'], 'state': 'FOCUS', 'foreground': True})
        actual = lab_runner.status(job['launch_id'])
        self.assertEqual(actual['state'], 'READY')
        self.assertTrue(actual['foreground'])
        self.assertEqual(actual['viewport']['width'], 1920)
        with self.assertRaises(ValueError):
            lab_runner.report({'launch_id': job['launch_id'], 'state': 'FOCUS', 'foreground': 'yes'})
        lab_runner.report({'launch_id': job['launch_id'], 'state': 'CLOSED'})
        lab_runner.report({'launch_id': job['launch_id'], 'state': 'FOCUS', 'foreground': False})
        self.assertEqual(lab_runner.status(job['launch_id'])['state'], 'CLOSED')

    def test_terminal_status_cannot_be_revived_by_late_browser_readiness(self):
        for state in ['FAILED', 'CLOSED']:
            job = lab_runner.launch('a' * 32)
            lab_runner.report({'launch_id': job['launch_id'], 'state': state, 'message': 'Session ended.'})
            lab_runner.report({'launch_id': job['launch_id'], 'state': 'READY'})
            self.assertEqual(lab_runner.status(job['launch_id'])['state'], state)
            self.assertEqual(lab_runner.status(job['launch_id'])['message'], 'Session ended.')

    def test_failed_build_and_unavailable_host_cannot_claim_launch_success(self):
        with patch.object(lab_runner.code_lab, 'saved_result', return_value={'ok': False}):
            with self.assertRaises(ValueError): lab_runner.launch('a' * 32)
        self.sender.assert_not_called()
        self.sender.return_value = False
        self.assertEqual(lab_runner.launch('b' * 32)['state'], 'FAILED')

    def test_unknown_launch_cannot_open_runner_or_update_status(self):
        with self.assertRaises(ValueError): lab_runner.runner_page('a' * 32)
        with self.assertRaises(ValueError): lab_runner.report({'launch_id': 'a' * 32, 'state': 'READY'})

    def test_runner_has_full_viewport_sandbox_and_no_development_shell(self):
        job = lab_runner.launch('a' * 32)
        page = lab_runner.runner_page(job['launch_id'])
        self.assertIn('sandbox="allow-scripts"', page)
        self.assertNotIn('allow-same-origin', page)
        self.assertIn('width:100%;height:100%', page)
        self.assertNotIn('page-shell.js', page)
        self.assertNotIn('Workbench', page)


if __name__ == '__main__': unittest.main()

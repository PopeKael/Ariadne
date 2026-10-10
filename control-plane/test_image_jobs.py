import threading
import time
import unittest

from image_jobs import ImageJobs, event_progress


class ImageJobTests(unittest.TestCase):
    def test_job_returns_before_render_and_survives_polling_with_idempotent_submit(self):
        jobs = ImageJobs()
        finish = threading.Event()
        entered = threading.Event()

        def generate(body, on_progress):
            on_progress(stage='Sampling image', step=3, total_steps=24)
            entered.set()
            finish.wait(3)
            return {'ok': True, 'image': {'filename': 'test.png'}}, 200

        result, status = jobs.start({'request_id': 'a' * 32}, generate)
        try:
            self.assertEqual(status, 202)
            self.assertTrue(entered.wait(1))
            current = jobs.snapshot(result['job']['id'])
            self.assertEqual(current['step'], 3)
            self.assertEqual(jobs.start({'request_id': 'a' * 32}, generate)[1], 202)
            self.assertEqual(jobs.start({'request_id': 'b' * 32}, generate)[1], 409)
        finally:
            finish.set()
        for _ in range(100):
            if jobs.snapshot()['state'] == 'completed':
                break
            time.sleep(.01)
        self.assertEqual(jobs.snapshot()['result']['image']['filename'], 'test.png')

    def test_error_is_retained_as_terminal_result(self):
        jobs = ImageJobs()
        jobs.start({}, lambda body, on_progress: ({'ok': False, 'message': 'Engine failed'}, 502))
        for _ in range(100):
            if jobs.snapshot()['state'] == 'error':
                break
            time.sleep(.01)
        self.assertEqual(jobs.snapshot()['result']['message'], 'Engine failed')

    def test_progress_filters_other_jobs_and_tracks_decode_separately(self):
        self.assertEqual(event_progress({'type': 'progress', 'data': {'prompt_id': 'other'}}, 'mine'), {})
        self.assertEqual(event_progress({'type': 'progress', 'data': {'prompt_id': 'mine', 'value': 12, 'max': 24}}, 'mine')['step'], 12)
        self.assertEqual(event_progress({'type': 'executing', 'data': {'prompt_id': 'mine', 'node': '8'}}, 'mine')['stage'], 'Decoding image')


if __name__ == '__main__':
    unittest.main()

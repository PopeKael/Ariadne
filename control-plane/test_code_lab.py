from __future__ import annotations
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch
import code_lab
from test_lab_verification import scripted_actions
from inference import InferenceRegistry

PROJECT = {"project_name": "Catch", "summary": "A browser game", "entrypoint": "index.html",
           "files": [{"path": "index.html", "content": '<!doctype html><html><button onclick="this.textContent=42">Play</button></html>'}]}


class CodeLabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'lab'
        self.environment = patch.dict(os.environ, {'ARIADNE_LAB_ROOT': str(self.root), 'ARIADNE_CODING_MODEL': 'test-coder', 'ARIADNE_CODING_CONTEXT': '32768'})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.registry = InferenceRegistry(Path(self.temp.name) / 'config.json')
        self.generate = Mock(return_value=iter([{'response': json.dumps(PROJECT), 'done': True, 'done_reason': 'stop',
            'prompt_eval_count': 100, 'prompt_eval_duration': 500_000_000, 'eval_count': 200, 'eval_duration': 2_000_000_000,
            'load_duration': 100_000_000, 'total_duration': 2_600_000_000}]))
        self.catalog = Mock(return_value={'available': True, 'models': [{'name': 'test-coder'}]})
        self.browser_patch=patch.object(code_lab.lab_verification,'request_browser',return_value={'passed':True,'errors':[],'checks':[{'passed':True}]})
        self.browser_patch.start();self.addCleanup(self.browser_patch.stop)
        def generate(*args):
            messages=args[2]['messages'][1:]
            observations=[json.loads(m['content'])['observation'] for m in messages if m['role']=='user' and m['content'].startswith('{"observation":')]
            value=scripted_actions(PROJECT['files'][0]['content'],observations)
            if value['action']=='write':value['project']=PROJECT
            return iter([{'response':json.dumps({'reason':'Scripted fixture action',**value}),'done':True,'done_reason':'stop',
                'prompt_eval_count':100,'prompt_eval_duration':500_000_000,'eval_count':200,'eval_duration':2_000_000_000,
                'load_duration':100_000_000,'total_duration':2_600_000_000}])
        self.generate.side_effect=generate
        self.lab = code_lab.CodeLab(self.registry, self.catalog, self.generate, nullcontext, lambda model: nullcontext(), lambda: {'gpu': None}, lambda state: None)

    def test_coding_route_independent_of_home_and_existing_saved_config(self):
        self.assertEqual(self.registry.route('coding').model_id, 'test-coder')
        self.assertEqual(self.registry.route('home_chat').provider_id, 'ollama-desktop')
        path = Path(self.temp.name) / 'saved.json'
        path.write_text(json.dumps({'inference': {'providers': [self.registry.route('home_chat').as_dict()]}}))
        saved = InferenceRegistry(path)
        self.assertEqual(saved.route('coding').model_id, 'test-coder')
        self.assertEqual(saved.route('home_chat').provider_id, 'ollama-desktop')

    def test_valid_generation_saves_exact_source_and_reproducible_inputs(self):
        events = []
        result, status = self.lab.run({'prompt': 'Build a game'}, events.append)
        self.assertEqual(status, 200)
        r = result['run']; directory = Path(r['project_path'])
        self.assertTrue(directory.is_relative_to(self.root))
        self.assertEqual((directory / 'index.html').read_bytes(), PROJECT['files'][0]['content'].encode())
        saved = json.loads((directory / 'run.json').read_text())
        self.assertEqual(saved['instructions'], code_lab.INSTRUCTIONS)
        self.assertEqual(saved['user_prompt'], 'Build a game')
        self.assertTrue(saved['success'])
        self.assertEqual(saved['request']['format']['oneOf'][0]['properties']['project'], code_lab.SCHEMA)
        self.assertEqual([e['state'] for e in events if e['type']=='state'], ['LOADING MODEL','GENERATING','EXECUTE','GENERATING','EXECUTE','GENERATING','EXECUTE','READY'])
        self.assertEqual(saved['request']['messages'][1]['content'],'Build a game')

    def test_saved_result_recovers_original_source_without_generating_again(self):
        result, _ = self.lab.run({'prompt': 'Build a game'},lambda e:None)
        recovered = code_lab.saved_result(result['run']['run_id'])
        self.assertEqual(recovered['run'], result['run'])
        self.assertEqual(code_lab.saved_result(), recovered)
        self.assertEqual(self.generate.call_count,3)
        with self.assertRaises(ValueError):
            code_lab.saved_result('../bad')

    def test_stream_heartbeats_during_silent_inference_and_returns_persisted_result(self):
        import threading
        release = threading.Event()
        original=self.generate.side_effect
        def slow_generate(*args):
            release.wait(timeout=3)
            yield from original(*args)
        self.generate.side_effect = slow_generate
        stream = code_lab.stream_events(self.lab, {'prompt': 'Build a game'}, heartbeat_seconds=0.01)
        try:
            first = next(stream)
            self.assertEqual(first['state'], 'LOADING MODEL')
            while next(stream)['type'] != 'heartbeat':
                pass
            release.set()
            events = list(stream)
            complete = events[-1]
            self.assertEqual(complete['type'], 'complete')
            self.assertTrue(complete['ok'])
            self.assertTrue((self.root / first['run_id'] / 'run.json').exists())
        finally:
            release.set()
            list(stream)

    def test_missing_model_is_recorded_without_generating(self):
        self.catalog.return_value = {'available': True, 'models': []}
        result, status = self.lab.run({'prompt': 'Build a game'})
        self.assertEqual(status, 409)
        self.assertIn('missing', result['message'])
        self.generate.assert_not_called()
        self.assertFalse(result['run']['success'])
        directory = self.root / result['run']['run_id']
        self.assertTrue((directory / 'run.json').is_file())
        self.assertFalse((directory / 'index.html').exists())

    def test_invalid_output_does_not_create_successful_project(self):
        for response in ['not json', '{}', json.dumps({**PROJECT, 'files': []})]:
            self.generate.side_effect=lambda *args: iter([{'response': response, 'done': True}])
            result, _ = self.lab.run({'prompt': 'Build a game'})
            self.assertFalse(result['ok'])
            self.assertIsNone(result['run']['project_path'])
            self.assertFalse((self.root / result['run']['run_id'] / 'index.html').exists())

    def test_v1_rejects_other_paths_and_multiple_files(self):
        for path in ['../index.html', 'a/index.html', 'INDEX.HTML', '/index.html', 'run.json', 'C:\\index.html']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                code_lab.validate_project({**PROJECT, 'files': [{'path': path, 'content': '<html></html>'}]})
        with self.assertRaises(ValueError):
            code_lab.validate_project({**PROJECT, 'files': PROJECT['files'] * 2})

    def test_root_and_run_containment(self):
        for value in ['../bad', 'x', 'a' * 31, 'z' * 32]:
            with self.assertRaises(ValueError): code_lab.run_directory(self.root, value)
        for root in [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent), 'relative']:
            with patch.dict(os.environ, {'ARIADNE_LAB_ROOT': root}), self.assertRaises(ValueError): code_lab.settings()
        # A junction/symlink resolving a valid run ID outside the root is rejected.
        with patch.object(Path, 'resolve', return_value=Path(self.temp.name) / 'outside'):
            with self.assertRaises(ValueError): code_lab.run_directory(self.root, 'a' * 32)

    def test_authoritative_timing_and_rates(self):
        result, _ = self.lab.run({'prompt': 'Build a game'},lambda e:None)
        t = result['run']['telemetry']
        self.assertEqual(t['total_duration'], 2_600_000_000)
        self.assertEqual(t['load_duration'], 100_000_000)
        self.assertEqual(t['prefill_tokens_per_second'], 200)
        self.assertEqual(t['eval_tokens_per_second'], 100)
        self.assertIsNone(code_lab.provider_metrics({'eval_count': 12, 'eval_duration': 0})['eval_tokens_per_second'])

    def test_lab_context_defaults_to_32k_and_rejects_smaller_values(self):
        with patch.dict(os.environ, {'ARIADNE_CODING_CONTEXT': ''}), patch.object(code_lab, '_read_saved', return_value={}):
            self.assertEqual(code_lab.settings()[1], 32768)
        with patch.dict(os.environ, {'ARIADNE_CODING_CONTEXT': '16384'}), self.assertRaises(ValueError):
            code_lab.settings()
        with self.assertRaises(ValueError):
            code_lab.validate_context(32768, {'available': True, 'context_max': 16384})

    def test_generation_uses_32k_context_and_pinned_residency(self):
        result, _ = self.lab.run({'prompt': 'Build a game'})
        request = self.generate.call_args.args[2]
        self.assertEqual(request['options']['num_ctx'], 32768)
        self.assertEqual(request['keep_alive'], -1)
        self.assertEqual(result['run']['context_tokens'], 32768)

    def test_prepare_unloads_then_loads_and_verifies_context(self):
        from contextlib import contextmanager
        events = []
        @contextmanager
        def transition(model):
            events.append(('guard', model))
            outcome = {'success': False}
            yield outcome
            self.assertTrue(outcome['success'])
        before = {'available': True, 'loaded': ['normal'], 'loaded_details': [{'name': 'normal'}]}
        after = {'available': True, 'loaded': ['coder'], 'loaded_details': [{'name': 'coder', 'context_length': 32768, 'size_vram': 123}]}
        preload = Mock(side_effect=lambda model, **kw: events.append(('load', model, kw)) or {'ok': True, 'response': {'load_duration': 123}})
        unload = Mock(side_effect=lambda model, **kw: events.append(('unload', model)) or True)
        remember = Mock()
        result, status = code_lab.prepare_working_model(
            {'model': 'coder', 'context_tokens': 32768, 'installed': True},
            catalog=Mock(side_effect=[before, after]), capabilities=lambda model: {'available': True, 'context_max': 32768},
            transition=transition, preload=preload, unload=unload, remember=remember,
            avatar=lambda state: None, unload_candidates={'normal'})
        self.assertEqual(status, 200)
        self.assertEqual(events[1], ('unload', 'normal'))
        self.assertEqual(events[2][0:2], ('load', 'coder'))
        self.assertEqual(events[2][2]['options']['num_ctx'], 32768)
        self.assertEqual(events[2][2]['keep_alive'], -1)
        self.assertEqual(result['load_duration'], 123)
        remember.assert_called_once_with('coder')

    def test_failed_preload_restores_previous_model(self):
        from contextlib import contextmanager
        @contextmanager
        def transition(model): yield {'success': False}
        preload = Mock(side_effect=[{'ok': False, 'detail': 'VRAM load failed'}, {'ok': True}])
        remember = Mock()
        result, status = code_lab.prepare_working_model(
            {'model': 'coder', 'context_tokens': 32768, 'installed': True},
            catalog=lambda: {'available': True, 'loaded': ['normal']},
            capabilities=lambda model: {'available': True, 'context_max': 32768},
            transition=transition, preload=preload, unload=Mock(return_value=True), remember=remember,
            avatar=lambda state: None, unload_candidates={'normal'})
        self.assertEqual(status, 409)
        self.assertIn('VRAM load failed', result['message'])
        self.assertEqual(preload.call_args.args[0], 'normal')
        remember.assert_not_called()

    def test_missing_or_incompatible_model_does_not_start_a_transition(self):
        transition = Mock()
        for installed, maximum in [(False, 32768), (True, 16384)]:
            result, status = code_lab.prepare_working_model(
                {'model': 'coder', 'context_tokens': 32768, 'installed': installed},
                catalog=Mock(), capabilities=lambda model: {'available': True, 'context_max': maximum},
                transition=transition, preload=Mock(), unload=Mock(), remember=Mock(),
                avatar=lambda state: None, unload_candidates={'normal'})
            self.assertFalse(result['ok'])
        transition.assert_not_called()

    def test_truncated_output_fails_even_if_valid_json(self):
        self.generate.side_effect=lambda *args: iter([{'response': json.dumps(PROJECT), 'done': True, 'done_reason': 'length'}])
        result, _ = self.lab.run({'prompt': 'Build a game'})
        self.assertFalse(result['ok'])

    def test_external_resources_and_navigation_markup_rejected(self):
        for markup in ['<script src="https://cdn.test/a.js"></script>', '<img src="/api/private">',
                       '<iframe src="data:text/html,x"></iframe>', '<meta http-equiv="refresh" content="0;url=https://example.test">',
                       '<style>@import "https://example.test";</style>']:
            with self.subTest(markup=markup), self.assertRaises(ValueError):
                code_lab.validate_project({**PROJECT, 'files': [{'path': 'index.html', 'content': '<html>' + markup + '</html>'}]})

    def test_preview_keeps_source_and_records_errors(self):
        result, _ = self.lab.run({'prompt': 'Build a game'},lambda e:None); run_id = result['run']['run_id']
        html = code_lab.preview_html(run_id, 'a' * 32)
        self.assertIn("connect-src 'none'", html)
        self.assertIn('unhandledrejection', html)
        self.assertIn("'DOMContentLoaded'", html)
        self.assertTrue(html.endswith(PROJECT['files'][0]['content']))
        value = code_lab.record_preview({'run_id': run_id, 'result': 'ERROR', 'errors': ['Oops']})
        self.assertEqual(value['run']['preview']['errors'], ['Oops'])
        self.assertEqual((Path(result['run']['project_path']) / 'index.html').read_text(), PROJECT['files'][0]['content'])

    def test_preview_rejects_failed_runs(self):
        self.catalog.return_value = {'available': True, 'models': []}
        result, _ = self.lab.run({'prompt': 'Build a game'})
        with self.assertRaises(ValueError): code_lab.preview_html(result['run']['run_id'], 'a' * 32)

    def test_ui_sandbox_and_explicit_lab_toggle(self):
        base = Path(__file__).parent
        js = (base / 'code-lab.js').read_text()
        html = (base / 'chat.html').read_text()
        self.assertIn('>The Lab</button>', html)
        self.assertIn("setAttribute('sandbox', 'allow-scripts')", js)
        self.assertNotIn('allow-same-origin', js)
        self.assertIn("event.source !== frame.contentWindow", js)
        self.assertIn("event.origin !== 'null'", js)
        self.assertIn("frame.csp = csp", js)
        self.assertIn("['Preview', preview], ['Code', code], ['Metrics', metrics]", js)


if __name__ == '__main__': unittest.main()

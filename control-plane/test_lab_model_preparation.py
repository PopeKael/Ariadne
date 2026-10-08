import unittest
from unittest.mock import patch
import server


class LabModelPreparationTests(unittest.TestCase):
    def setUp(self):
        self.previous = {name: getattr(server, name) for name in (
            'GPU_OWNER', 'GPU_TRANSITION_STATE', 'GPU_TRANSITION_DETAIL', 'GPU_TRANSITION_OPERATION',
            'GPU_TRANSITION_STARTED_AT', 'GPU_VAULT_RESERVATION', 'GPU_AI_ADMISSIONS', 'LAB_RESIDENT_MODEL')}
        server.GPU_OWNER = 'AI'; server.GPU_TRANSITION_STATE = 'IDLE'
        server.GPU_VAULT_RESERVATION = None; server.GPU_AI_ADMISSIONS = 0; server.LAB_RESIDENT_MODEL = None

    def tearDown(self):
        for name, value in self.previous.items(): setattr(server, name, value)

    def test_guard_blocks_existing_work_without_leaking_lock_or_state(self):
        for field, value in [('GPU_OWNER', 'RENDERER'), ('GPU_VAULT_RESERVATION', 'vault'), ('GPU_AI_ADMISSIONS', 1)]:
            with patch.object(server, field, value):
                with self.assertRaises(RuntimeError):
                    with server.lab_model_transition('coder'): self.fail('GPU guard admitted a conflicting workload')
            self.assertEqual(server.GPU_TRANSITION_STATE, 'IDLE')
            self.assertTrue(server.MODEL_SWITCH_LOCK.acquire(blocking=False)); server.MODEL_SWITCH_LOCK.release()

    def test_guard_reuses_current_gpu_transition_states(self):
        with server.lab_model_transition('coder') as outcome:
            self.assertEqual(server.GPU_OWNER, 'TRANSITION')
            self.assertEqual(server.GPU_TRANSITION_STATE, 'SWITCHING_MODEL')
            outcome['success'] = True
        self.assertEqual(server.GPU_OWNER, 'AI')
        self.assertEqual(server.GPU_TRANSITION_STATE, 'IDLE')

    def test_prepare_keeps_conversational_routes_and_configuration(self):
        normal_model, planner = server.HOME_CHAT_MODEL, server.PLANNER_MODEL
        routes = dict(server.INFERENCE_REGISTRY.routes)
        provider = server.INFERENCE_REGISTRY.route('coding')
        config = {'model': provider.model_id, 'context_tokens': 32768, 'installed': True}
        fake_service = type('FakeService', (), {'status': lambda self: config})()
        with patch.object(server, 'code_lab_service', return_value=fake_service), \
             patch.object(server.code_lab, 'prepare_working_model', return_value=({'ok': True}, 200)) as prepare, \
             patch.object(server, 'save_configuration') as save:
            result, status = server.prepare_code_lab_model({'enabled': True})
        self.assertEqual(status, 200)
        self.assertEqual(prepare.call_args.args[0]['context_tokens'], 32768)
        self.assertTrue(prepare.call_args.args[0]['lab_mode'])
        self.assertEqual(server.HOME_CHAT_MODEL, normal_model)
        self.assertEqual(server.PLANNER_MODEL, planner)
        self.assertEqual(server.INFERENCE_REGISTRY.routes, routes)
        save.assert_not_called()

    def test_mode_model_is_preserved_under_memory_pressure(self):
        server.LAB_RESIDENT_MODEL = 'coder'
        with patch.object(server, 'ollama_catalog', return_value={'available': True, 'loaded_details': []}), \
             patch.object(server, 'gpu_status', return_value={'available': True, 'total_gb': 16, 'free_gb': 0.5}), \
             patch.object(server, 'release_idle_ollama_models', return_value={'unloaded': []}) as release:
            server.monitor_ollama_models()
        self.assertEqual(release.call_args.kwargs['preserve_models'], {server.HOME_CHAT_MODEL, 'coder'})


if __name__ == '__main__': unittest.main()

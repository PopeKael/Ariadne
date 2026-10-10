import base64
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
import rabbit_assessment as assessment

PROFILE = dict(os='Windows', gpu='AMD Radeon RX 7800 XT', architecture='gfx1101 / RDNA3', vram_gb=16)


def inspection(text='', issues=None, errors=None):
    return dict(documents=[dict(label='README',url='https://github.com/example/tool/blob/main/README.md',text=text)] + (issues or []),
                latest_release=None, checked=['README','Releases','Recent issues'], errors=errors or [], checked_at='2026-10-10T00:00:00Z')


class AssessmentTests(unittest.TestCase):
    def test_reused_http_evidence_keeps_original_fetch_time(self):
        def read(url, **kwargs):
            assessment.BUDGET.read_context.last = (url, 1791500000)
            return (json.dumps(dict(encoding='base64',content=base64.b64encode(b'Local inference with Ollama').decode())).encode() if url.endswith('/readme') else b'[]'), 'utf-8'
        with patch.object(assessment,'read_url',side_effect=read):
            result = assessment.inspect_repository('example/cached-evidence')
        self.assertEqual(result['checked_at'],assessment.datetime.fromtimestamp(1791500000,assessment.timezone.utc).isoformat(timespec='seconds'))

    def test_local_demonstration_gets_episode_idea_without_certifying_fit(self):
        candidate = dict(description='Whisper speech transcription')
        local = assessment.assess(candidate, inspection('Run speech transcription locally offline on Windows with Vulkan.'), PROFILE)
        hosted = assessment.assess(candidate, inspection('Upload your recording to our cloud service.'), PROFILE)
        self.assertIn('Transcribe',local['episode_idea'])
        self.assertIsNone(hosted['episode_idea'])
        self.assertGreater(local['selection_score'],hosted['selection_score'])
        blocked = assessment.assess(candidate, inspection('Offline speech transcription requires CUDA.'), PROFILE)
        self.assertIsNone(blocked['episode_idea'])

    def test_known_quota_exhaustion_stops_extra_repository_requests(self):
        failure = HTTPError('https://api.github.com/repos/example/tool/readme',403,'Forbidden',{'X-RateLimit-Remaining':'0'},None)
        with patch.object(assessment,'read_url',side_effect=failure) as read:
            result = assessment.inspect_repository('example/tool')
        self.assertEqual(read.call_count,1)
        self.assertIn('allowance is exhausted',' '.join(result['errors']))

    def test_documented_machine_connection_outweighs_another_gpu_target(self):
        sources = inspection('Ollama inference on Windows. AMD GPU reference: RX 7800 XT / gfx1101.')
        ours = assessment.assess(dict(description='Inference for RX 7800 XT gfx1101'), sources, PROFILE)
        other = assessment.assess(dict(description='Ollama build for MI50 gfx906'), sources, PROFILE)
        self.assertTrue(ours['relevant'])
        self.assertGreater(ours['selection_score'], other['selection_score'])
        self.assertFalse(other['relevant'])
        self.assertEqual(other['recommendation'], 'Ignore')
        self.assertIn('explicitly names', ours['why_care'])
        self.assertEqual(ours['recommendation'], 'Watch')

    def test_python_and_keywords_do_not_prove_compatibility(self):
        result=assessment.assess(dict(description='A Python AI agent'),inspection('Windows support is planned. AMD support is not available.'),PROFILE)
        self.assertEqual(result['recommendation'],'Watch')
        self.assertEqual(result['evidence'][1]['status'],'Mentioned in documentation')
        self.assertNotIn('supported',result['summary'].lower())
        self.assertTrue(all(e['status'].startswith('Not stated') for e in result['evidence'][2:]))

    def test_explicit_nvidia_requirement_and_optional_cuda(self):
        blocked=assessment.assess(dict(description="An AI agent"),inspection('Requires NVIDIA CUDA hardware.'),PROFILE)
        self.assertEqual(blocked['recommendation'],'Ignore')
        self.assertIn('AMD',blocked['reason'])
        optional=assessment.assess(dict(description="An AI agent"),inspection('Optional acceleration requires NVIDIA CUDA. CPU mode also works.'),PROFILE)
        self.assertEqual(optional['recommendation'],'Watch')
        self.assertFalse(optional['blockers'])

    def test_minimum_vram_is_compared_to_configured_budget(self):
        result=assessment.assess(dict(description="An AI agent"),inspection('Minimum 24 GB VRAM required.'),PROFILE)
        self.assertEqual(result['recommendation'],'Ignore')
        self.assertIn('16 GB',result['reason'])
        result=assessment.assess(dict(description="An AI agent"),inspection('Minimum 24 GB VRAM required.'),dict(PROFILE,vram_gb=32))
        self.assertEqual(result['recommendation'],'Watch')

    def test_open_issue_is_a_report_not_a_verified_failure(self):
        issue=dict(label='Recent issues',url='https://github.com/example/tool/issues/3',text='Windows AMD crashes',state='open')
        result=assessment.assess(dict(description="An AI agent"),inspection('MCP integration',issues=[issue]),PROFILE)
        self.assertEqual(result['recommendation'],'Watch')
        self.assertEqual(result['concerns'][0]['url'],issue['url'])
        issue['state']='closed'
        self.assertFalse(assessment.assess(dict(description="An AI agent"),inspection(issues=[issue]),PROFILE)['concerns'])

    def test_partial_collection_does_not_claim_completeness(self):
        result=assessment.assess(dict(description="An AI agent"),inspection('Requires CUDA.',errors=['README: GitHub HTTP 403']),PROFILE)
        self.assertEqual(result['recommendation'],'Watch')
        self.assertIn('incomplete',result['reason'])

    def test_unrelated_project_and_local_variable_are_not_local_ai(self):
        result=assessment.assess(dict(description='An experimental compiler'),inspection('Use a local variable for this option.'),PROFILE)
        self.assertEqual(result['recommendation'],'Ignore')
        result=assessment.assess(dict(description='An agent service'),inspection('MCP integration is available.'),PROFILE)
        self.assertEqual(result['recommendation'],'Watch')
        self.assertIn('MCP',result['why_care'])

    def test_wrapped_windows_negation_is_kept_in_context(self):
        result=assessment.assess(dict(description='A compiler'),inspection('Platforms: Linux and macOS. Windows is not\navailable yet.'),PROFILE)
        self.assertEqual(result['recommendation'],'Watch')
        self.assertIn('native Windows',result['reason'])
        self.assertIn('not available',result['evidence'][0]['quote'])

    def test_collection_is_bounded_and_retains_release_dates(self):
        requested=[]
        def read(url,**kwargs):
            requested.append(url)
            if url.endswith('/readme'):
                payload=dict(encoding='base64',content=base64.b64encode(b'Windows and MCP support.').decode(),html_url='https://github.com/example/tool/blob/main/README.md')
            elif '/releases?' in url:
                payload=[dict(tag_name='v0.2',published_at='2026-10-09T00:00:00Z',html_url='https://github.com/example/tool/releases/tag/v0.2',prerelease=True,body='AMD experimental')]
            else:
                payload=[dict(pull_request={},html_url='https://github.com/example/tool/pull/1'),dict(title='AMD crash',state='open',html_url='https://github.com/example/tool/issues/2')]
            return json.dumps(payload).encode(),'utf-8'
        with patch.object(assessment,'read_url',side_effect=read): result=assessment.inspect_repository('example/tool')
        self.assertEqual(len(requested),3)
        self.assertIn('per_page=3',requested[1])
        self.assertIn('per_page=5',requested[2])
        self.assertEqual(result['latest_release']['tag'],'v0.2')
        self.assertTrue(result['latest_release']['prerelease'])
        self.assertEqual(len(result['documents']),3)

    def test_missing_documents_and_network_errors_are_distinguished(self):
        with patch.object(assessment,'read_url',side_effect=HTTPError('url',404,'Not found',{},None)):
            result=assessment.inspect_repository('example/tool')
        self.assertEqual(len(result['checked']),3)
        self.assertFalse(result['errors'])
        with patch.object(assessment,'read_url',side_effect=TimeoutError):
            result=assessment.inspect_repository('example/tool')
        self.assertEqual(len(result['errors']),3)
        self.assertFalse(result['checked'])

    def test_unchanged_evidence_is_reused_but_changes_and_failures_are_not_hidden(self):
        from datetime import datetime, timezone
        data=inspection('MCP integration')
        data['checked_at']=datetime.now(timezone.utc).isoformat()
        candidate=dict(repository_name='example/tool',description='An AI agent',metrics=dict(pushed_at='one',updated_at='one'))
        with tempfile.TemporaryDirectory() as temp, patch.object(assessment,'CACHE_ROOT',Path(temp)), patch.object(assessment,'inspect_repository',return_value=data) as collect:
            assessment.enrich(candidate)
            assessment.enrich(candidate)
            self.assertEqual(collect.call_count,1)
            candidate['metrics']['pushed_at']='two'
            assessment.enrich(candidate)
            self.assertEqual(collect.call_count,2)
            candidate['metrics']['pushed_at']='three'
            collect.return_value=inspection(errors=['GitHub HTTP 403'])
            assessment.enrich(candidate)
            assessment.enrich(candidate)
            self.assertEqual(collect.call_count,4)
            self.assertIn('incomplete',candidate['assessment']['reason'])
            self.assertIn('Last successful evidence retained',candidate['assessment']['summary'])

if __name__ == '__main__': unittest.main()

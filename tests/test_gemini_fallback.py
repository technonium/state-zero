"""Text-model swap preserves provider settings and timing; no real calls."""
import tests
import os
import unittest
from unittest.mock import Mock, patch
from openrouter_client import OpenRouterClient, call_google_gemini_generate_content
from state_zero_models import GEMINI_MODEL


class GeminiFallbackModelTests(unittest.TestCase):
    def test_new_model_exact_same_gemini_settings_and_timeout(self):
        response=Mock();response.json.return_value={'candidates':[{'content':{'parts':[{'text':'ok'}]}}]}
        with patch('openrouter_client.requests.post',return_value=response) as post,patch.dict(os.environ,{},clear=True):
            self.assertEqual(call_google_gemini_generate_content(prompt='unchanged prompt',api_key='fixture'), 'ok')
        self.assertEqual(GEMINI_MODEL,'gemini-3.8-flash')
        self.assertIn('/models/gemini-3.8-flash:generateContent',post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs['timeout'],90)
        self.assertEqual(post.call_args.kwargs['json']['generationConfig'],{'temperature':1.0,'maxOutputTokens':12000,'thinkingConfig':{'thinkingBudget':8000}})
        self.assertEqual(post.call_args.kwargs['json']['contents'][0]['parts'][0]['text'],'unchanged prompt')
        for configured,expected in ((None,120),('200',200)):
            with patch.dict(os.environ,{} if configured is None else {'OPENROUTER_CALL_DEADLINE_SECONDS':configured},clear=True), \
                    patch('openrouter_client.requests.post',return_value=response) as post:
                OpenRouterClient(api_key='fixture')._post_openrouter_request('https://openrouter.ai/api/v1/chat/completions',{}, {})
            self.assertEqual(post.call_args.kwargs['timeout'],expected)
        self.assertEqual(OpenRouterClient.DEFAULT_MODEL,'minimax/minimax-m2.5')

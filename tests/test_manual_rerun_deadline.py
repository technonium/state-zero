"""Explicit manual reruns retain bounded recovery after the scheduled cutoff."""
import tests
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from flow_media_client import FlowMediaClient
from pipeline import WHOOPPipeline


class ManualRerunDeadlineTests(unittest.TestCase):
    def test_afternoon_recovery_retrieves_only_in_from_now_mode(self):
        now=datetime(2026,11,14,15,30,tzinfo=ZoneInfo('Asia/Kolkata'))
        for mode in ('from_now','run_date'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{
                    'STATE_ZERO_PRIVATE_ROOT':tmp,'PIPELINE_TIMEZONE':'Asia/Kolkata',
                    'PIPELINE_MANUAL_DEADLINE_MODE':mode,'PIPELINE_MANUAL_WINDOW_MINUTES':'150',
                    'PIPELINE_EFFECTIVE_DEADLINE_ISO':(now+timedelta(minutes=150)).isoformat(),
                    'FLOW_PREFLIGHT_PROJECT_ID':'project'},clear=True), \
                    patch('utils.get_pipeline_now',return_value=now), \
                    patch('flow_media_client.time.time',return_value=now.timestamp()):
                root=Path(tmp);context=root/'flow_video_attempt_1.context.json';context.write_text('{}')
                output=root/'generated_video.mp4';diag=root/'flow_video_diagnostics.json'
                client=FlowMediaClient();client._write_diagnostics(diag,{'request_hash':'request'})
                receipt={'request_hash':'request','profile':client.profile,'project_id':'project',
                         'media_id':'clip','workflow_id':'workflow'}
                candidate={'kind':'video','context_sha256':hashlib.sha256(context.read_bytes()).hexdigest(),
                           'observed_at':now.timestamp(),'result':receipt}
                modes=[]
                def worker(command,timeout):
                    kind='search' if 'search' in command else 'retrieve';modes.append(kind)
                    if kind=='search':return 0,{'candidate':candidate}
                    raw=root/'flow_recovered/original.mp4';raw.write_bytes(b'exact original')
                    return 0,{'result':receipt|{'local_path':str(raw),'sha256':hashlib.sha256(raw.read_bytes()).hexdigest()}}
                with patch.object(client,'_check_browser_ownership'),patch.object(client,'_notify_transition'), \
                        patch.object(client,'_local_result',return_value=receipt), \
                        patch.object(client,'_recovery_command',side_effect=worker):
                    result=client._readback_media(context,'request',output,now.timestamp()+300,stage='video')
                retrieval=client._read_diagnostics(diag)['retrieval']
                if mode=='from_now':
                    self.assertIsNotNone(result);self.assertEqual(modes,['search','retrieve'])
                    self.assertEqual(retrieval['attempts'],1)
                    self.assertEqual(retrieval['deadline'],now.timestamp()+270)
                    self.assertTrue((root/'flow_video_attempt_1.result.json').exists())
                else:
                    self.assertIsNone(result);self.assertEqual(modes,['search'])
                    self.assertEqual(retrieval['attempts'],0)
                    self.assertEqual(retrieval['deadline'],now.replace(hour=13,minute=30).timestamp())

    def test_from_now_deadline_is_anchored_for_parent_and_children(self):
        now=datetime(2026,11,14,18,tzinfo=ZoneInfo('Asia/Kolkata'))
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{
                'STATE_ZERO_PRIVATE_ROOT':tmp,'PIPELINE_MANUAL_DEADLINE_MODE':'from_now',
                'PIPELINE_MANUAL_WINDOW_MINUTES':'150','PIPELINE_MEDIA_MODE':'local_test',
                'PIPELINE_POST_TO_INSTAGRAM':'false'},clear=True), \
                patch.object(WHOOPPipeline,'_now',return_value=now), \
                patch('utils.get_pipeline_now',return_value=now), \
                patch('flow_media_client.time.time',return_value=now.timestamp()):
            pipeline=WHOOPPipeline()
            self.assertEqual(os.environ['PIPELINE_EFFECTIVE_DEADLINE_ISO'],pipeline.deadline_dt.isoformat())
            self.assertEqual(FlowMediaClient._generation_seconds(),7200)
            later=now+timedelta(minutes=10)
            with patch('utils.get_pipeline_now',return_value=later),patch('flow_media_client.time.time',return_value=later.timestamp()):
                self.assertEqual(FlowMediaClient._generation_seconds(),6600)

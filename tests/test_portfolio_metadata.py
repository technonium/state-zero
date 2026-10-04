import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
from portfolio_metadata import build_metadata, publish_metadata

class MetadataTests(unittest.TestCase):
    def payload(self, **kwargs):
        args = dict(date='2026-10-04', title='Actual title', post_result={'post_id':'123', 'permalink':'https://www.instagram.com/p/abc/', 'mock':False}, base_url='https://media.example.test/media')
        args.update(kwargs)
        return build_metadata(**args)

    def test_exact_contract(self):
        p = self.payload()
        self.assertEqual(set(p), {'schemaVersion','date','title','instagramUrl','lightVideoUrl','darkVideoUrl'})
        self.assertEqual(p['darkVideoUrl'], 'https://media.example.test/media/portfolio/2026-10-04/dark.mp4')

    def test_invalid_inputs(self):
        for change in [dict(post_result={'post_id':'123','permalink':'https://instagram.com/p/'}), dict(post_result={'post_id':'123','permalink':'https://instagram.com:443/p/abc/'}), dict(title=''), dict(date='../bad'), dict(base_url='https://mock-vps.com'), dict(base_url=''), dict(post_result=None), dict(post_result={'post_id':'123','permalink':'https://evil.test/p/a'}), dict(post_result={'post_id':'123','permalink':'https://instagram.com/p/a','mock':True})]:
            with self.subTest(change=change), self.assertRaises(ValueError): self.payload(**change)

    def test_local_atomic_publication(self):
        with tempfile.TemporaryDirectory() as t:
            publish_metadata(self.payload(), Path(t))
            a=Path(t)/'portfolio/2026-10-04/metadata.json'
            b=Path(t)/'portfolio/latest/metadata.json'
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(json.loads(b.read_text()), self.payload())

    def test_failed_latest_preserves_previous(self):
        with tempfile.TemporaryDirectory() as t:
            latest=Path(t)/'portfolio/latest/metadata.json'
            latest.parent.mkdir(parents=True); latest.write_text('previous')
            import os
            replace=os.replace
            def fail(src,dst):
                if Path(dst)==latest: raise OSError('failed')
                return replace(src,dst)
            with patch('portfolio_metadata.os.replace', side_effect=fail), self.assertRaises(OSError): publish_metadata(self.payload(), Path(t))
            self.assertEqual(latest.read_text(), 'previous')

    def test_ssh_uses_temporary_upload_then_rename(self):
        with patch('portfolio_metadata.subprocess.run') as run:
            run.return_value.returncode=0
            publish_metadata(self.payload(), Path('/srv/media'), target='user@server', ssh_opts=[])
            calls=[c.args[0] for c in run.call_args_list]
            self.assertEqual([c[0] for c in calls], ['ssh','scp','ssh','ssh','scp','ssh'])
            self.assertIn('mv -f', calls[-1][-1])
            self.assertIn('/portfolio/latest/', calls[-1][-1])

class PipelineMetadataTests(unittest.TestCase):
    def pipeline(self):
        from pipeline import WHOOPPipeline
        p=WHOOPPipeline.__new__(WHOOPPipeline)
        p.post_to_instagram=True; p.portfolio_media_enabled=True
        p.run_date='2026-10-04'; p.media_mode='local_test'
        p.output_dir=Path('/unused')
        return p

    def test_unavailable_dated_video_does_not_write(self):
        p=self.pipeline()
        with patch.dict('os.environ', {'VPS_PUBLIC_BASE_URL':'https://media.example.test'}), patch.object(p, '_ensure_public_urls_reachable', side_effect=RuntimeError('unavailable')), patch('pipeline.publish_metadata') as publish:
            with self.assertRaises(RuntimeError): p._publish_portfolio_metadata(post_result={'post_id':'123','permalink':'https://instagram.com/p/abc/'}, title='Title')
            publish.assert_not_called()

    def test_disabled_posting_does_not_write(self):
        p=self.pipeline(); p.post_to_instagram=False
        with patch('pipeline.publish_metadata') as publish:
            p._publish_portfolio_metadata(post_result=None,title=None)
            publish.assert_not_called()

    def test_normal_and_fallback_use_actual_title_and_result(self):
        from unittest.mock import Mock
        for fallback in (False, True):
            p=self.pipeline(); result={'post_id':'123','permalink':'https://instagram.com/p/abc/'}
            title='Fallback actual' if fallback else 'Normal actual'
            manager=Mock() if fallback else None
            with patch('pipeline.get_notifier'), patch.object(p,'step_16_render_portfolio_media',return_value=Path('/unused')), patch.object(p,'step_17_upload_portfolio_vps',return_value={'dark.mp4':'https://media.test/dark.mp4'}), patch.object(p,'_publish_portfolio_metadata') as publish:
                p._run_portfolio_media_secondary(art_path=Path('/art'),video_path=Path('/video'),fallback_manager=manager,post_result=result,title=title)
                publish.assert_called_once_with(post_result=result,title=title)

    def test_metadata_and_alert_failure_remain_secondary(self):
        from unittest.mock import Mock
        p=self.pipeline(); notifier=Mock(); notifier.notify_warning.side_effect=RuntimeError('alert failed')
        with patch('pipeline.get_notifier', return_value=notifier), patch.object(p,'step_16_render_portfolio_media',return_value=Path('/unused')), patch.object(p,'step_17_upload_portfolio_vps',return_value={'dark.mp4':'url'}), patch.object(p,'_publish_portfolio_metadata',side_effect=RuntimeError('metadata failed')):
            p._run_portfolio_media_secondary(art_path=Path('/art'),video_path=Path('/video'))
        notifier.notify_warning.assert_called_once()

    def test_notifier_initialization_failure_remains_secondary(self):
        p=self.pipeline()
        with patch('pipeline.get_notifier', side_effect=RuntimeError('notifier init failed')), patch.object(p,'step_16_render_portfolio_media',side_effect=RuntimeError('render failed')):
            p._run_portfolio_media_secondary(art_path=Path('/art'),video_path=Path('/video'))

    def test_upload_failure_never_publishes_metadata(self):
        p=self.pipeline()
        with patch('pipeline.get_notifier'), patch.object(p,'step_16_render_portfolio_media',return_value=Path('/unused')), patch.object(p,'step_17_upload_portfolio_vps',side_effect=RuntimeError('upload failed')), patch.object(p,'_publish_portfolio_metadata') as publish:
            p._run_portfolio_media_secondary(art_path=Path('/art'),video_path=Path('/video'))
            publish.assert_not_called()

if __name__=='__main__': unittest.main()

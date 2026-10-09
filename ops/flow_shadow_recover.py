#!/usr/bin/env python3
"""Resume shadow media from saved inputs; invoked only by the locked runner."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'scripts'))


def validate_recovery_inputs(output):
    for stage, canonical in (("image", "generated_art.png"), ("video", "generated_video.mp4")):
        if (output / canonical).is_file():
            continue
        diagnostics = output / f"flow_{stage}_diagnostics.json"
        try:
            saved = json.loads(diagnostics.read_text())
        except (OSError, ValueError):
            saved = {}
        # The adapter verifies request identity and recovers this result without submitting.
        if saved.get("request_hash") and saved.get("result"):
            continue
        for marker in output.glob(f"flow_{stage}_attempt_*.submit"):
            try:
                state = json.loads(marker.read_text()).get("state")
            except (ValueError, OSError):
                state = "uncertain"
            if state != "blocked_before_submission":
                raise ValueError(f"Reconcile existing {stage} submission before recovery")


def recover_media(pipeline):
    if pipeline.post_to_instagram:
        raise ValueError('Shadow recovery cannot publish')
    output = pipeline.output_dir
    validate_recovery_inputs(output)
    daily = json.loads((output / 'daily_data.json').read_text())
    metadata = json.loads((output / 'card_metadata.json').read_text())
    image_prompt = json.loads((output / 'image_prompt.json').read_text())
    video_prompt = output / 'video_prompt.txt'
    if not video_prompt.read_text().strip():
        raise ValueError('Saved video prompt missing')
    art = pipeline.step_7_generate_image(image_prompt)
    video = pipeline.step_9_generate_video(art, video_prompt)
    image_card = pipeline.step_10a_render_image(art, daily, metadata)
    video_card = pipeline.step_10b_render_video(video, daily, metadata)
    pipeline.step_16_render_portfolio_media(art, video)
    pipeline.step_15_archive(daily, metadata, image_card, video_card, image_prompt, 'dry-run',
                            blend_option=(output / 'blend_option.txt').read_text().strip() if (output / 'blend_option.txt').exists() else None,
                            creature=(output / 'creature_selected.txt').read_text().strip() if (output / 'creature_selected.txt').exists() else None,
                            environment=(output / 'environment_selected.txt').read_text().strip() if (output / 'environment_selected.txt').exists() else None)


if __name__ == '__main__':
    if os.getenv('FLOW_SHADOW_RECOVERY_LOCKED') != 'true':
        raise SystemExit('Use flow_shadow_run.py --recover-date YYYY-MM-DD')
    from pipeline import WHOOPPipeline
    recover_media(WHOOPPipeline())

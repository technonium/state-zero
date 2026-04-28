import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import utils
import validate
from ops import check_dokploy_deployment_safety
from pipeline import PipelineStageError, WHOOPPipeline


class DeploymentEnvHardeningTests(unittest.TestCase):
    def _base_live_vps_env(self) -> dict[str, str]:
        return {
            "PIPELINE_MODE": "automatic",
            "PIPELINE_POST_TO_INSTAGRAM": "true",
            "PIPELINE_MEDIA_MODE": "live_vps",
            "OPENROUTER_API_KEY": "openrouter",
            "GOOGLE_API_KEY_PRIMARY": "google-primary",
            "WHOOP_CLIENT_ID": "whoop-client",
            "WHOOP_CLIENT_SECRET": "whoop-secret",
            "INSTAGRAM_ACCESS_TOKEN": "ig-token",
            "INSTAGRAM_USER_ID": "ig-user",
            "VPS_PUBLIC_BASE_URL": "https://media.example.com",
            "VPS_SSH_HOST": "203.0.113.10",
            "VPS_SSH_USER": "root",
            "VPS_SSH_PATH": "/srv/example-media",
            "EMERGENCY_FALLBACK_ENABLED": "false",
        }

    def test_load_project_dotenv_preserves_existing_env_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "requirements.txt").write_text("", encoding="utf-8")
            (root / ".env").write_text("VPS_SSH_HOST=localhost\n", encoding="utf-8")

            with patch.object(utils, "get_project_root", return_value=root):
                with patch.dict(os.environ, {"VPS_SSH_HOST": "203.0.113.10"}, clear=False):
                    utils.load_project_dotenv()
                    self.assertEqual(os.environ["VPS_SSH_HOST"], "203.0.113.10")

    def test_get_private_root_prefers_hyphenated_sibling_layout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "project"
            project_root.mkdir()
            (project_root / "requirements.txt").write_text("", encoding="utf-8")
            private_root = project_root.parent / "project-private"
            (private_root / "runtime").mkdir(parents=True)

            with patch.object(utils, "get_project_root", return_value=project_root):
                self.assertEqual(utils.get_private_root(), private_root)

    def test_get_private_root_still_supports_legacy_spaced_sibling_layout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "project"
            project_root.mkdir()
            (project_root / "requirements.txt").write_text("", encoding="utf-8")
            legacy_private_root = project_root.parent / "project Private"
            (legacy_private_root / "astrology").mkdir(parents=True)

            with patch.object(utils, "get_project_root", return_value=project_root):
                self.assertEqual(utils.get_private_root(), legacy_private_root)

    def test_validate_environment_rejects_localhost_live_vps_host(self):
        env = self._base_live_vps_env()
        env["VPS_SSH_HOST"] = "localhost"

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                validate.validate_environment(rescue_only=False)

        self.assertEqual(ctx.exception.code, 1)

    def test_validate_environment_rejects_local_users_path_in_live_vps_mode(self):
        env = self._base_live_vps_env()
        env["VPS_SSH_PATH"] = str(Path.home() / "example" / "local_vps")

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                validate.validate_environment(rescue_only=False)

        self.assertEqual(ctx.exception.code, 1)

    def test_validate_environment_rejects_relative_live_vps_path(self):
        env = self._base_live_vps_env()
        env["VPS_SSH_PATH"] = "relative/upload-dir"

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                validate.validate_environment(rescue_only=False)

        self.assertEqual(ctx.exception.code, 1)

    def test_validate_environment_rejects_tmp_live_vps_path(self):
        env = self._base_live_vps_env()
        env["VPS_SSH_PATH"] = "/tmp/state-zero-media"

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                validate.validate_environment(rescue_only=False)

        self.assertEqual(ctx.exception.code, 1)

    def test_validate_environment_rejects_loopback_alias_in_live_vps_mode(self):
        env = self._base_live_vps_env()
        env["VPS_SSH_HOST"] = "127.0.0.42"

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                validate.validate_environment(rescue_only=False)

        self.assertEqual(ctx.exception.code, 1)

    def test_validate_environment_rescue_only_skips_generation_credentials(self):
        env = {
            "PIPELINE_MODE": "automatic",
            "PIPELINE_POST_TO_INSTAGRAM": "true",
            "PIPELINE_MEDIA_MODE": "live_vps",
            "INSTAGRAM_ACCESS_TOKEN": "ig-token",
            "INSTAGRAM_USER_ID": "ig-user",
            "EMERGENCY_FALLBACK_ENABLED": "true",
            "PIPELINE_TERMINAL_RESCUE_RUN": "true",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch.object(validate, "validate_emergency_fallback_readiness", return_value=None):
                validate.validate_environment()

    def test_validate_terminal_rescue_is_inferred_from_deadline(self):
        env = {
            "PIPELINE_TIMEZONE": "Asia/Kolkata",
            "PIPELINE_DATE": "2026-04-18",
            "PIPELINE_MANUAL_DEADLINE_LOCAL": "14:00",
            "PIPELINE_TERMINAL_RESCUE_RUN": "false",
        }

        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(
                validate.is_terminal_rescue_run(
                    now=datetime(2026, 4, 18, 14, 0, 3, tzinfo=ZoneInfo("Asia/Kolkata"))
                )
            )

    def test_validate_terminal_rescue_respects_from_now_deadline_mode(self):
        env = {
            "PIPELINE_TIMEZONE": "Asia/Kolkata",
            "PIPELINE_DATE": "2026-04-18",
            "PIPELINE_MANUAL_DEADLINE_MODE": "from_now",
            "PIPELINE_MANUAL_WINDOW_MINUTES": "30",
            "PIPELINE_TERMINAL_RESCUE_RUN": "false",
        }

        now = datetime(2026, 4, 18, 14, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(validate.is_terminal_rescue_run(now=now))

    def test_step_12_upload_vps_rejects_invalid_live_vps_config_before_ssh(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.run_date = "2026-04-10"
        pipeline.post_to_instagram = True
        pipeline.media_mode = "live_vps"
        pipeline.local_vps_dir = Path(tempfile.gettempdir()) / "project-local-vps"
        pipeline._set_heartbeat_context = lambda **kwargs: None

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "card_final.mp4"
            thumb_path = Path(tmpdir) / "card_final.png"
            video_path.write_bytes(b"video")
            thumb_path.write_bytes(b"thumb")

            env = self._base_live_vps_env()
            env["VPS_SSH_HOST"] = "localhost"

            with patch.dict(os.environ, env, clear=True):
                with patch("pipeline.subprocess.run") as run_mock:
                    with self.assertRaises(PipelineStageError) as ctx:
                        WHOOPPipeline.step_12_upload_vps(pipeline, video_path, thumb_path)

        self.assertEqual(ctx.exception.stage, "VPS Upload")
        self.assertIn("VPS_SSH_HOST resolves to localhost", ctx.exception.message)
        run_mock.assert_not_called()

    def test_step_12_upload_vps_rejects_relative_live_vps_path_before_ssh(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.run_date = "2026-04-10"
        pipeline.post_to_instagram = True
        pipeline.media_mode = "live_vps"
        pipeline.local_vps_dir = Path(tempfile.gettempdir()) / "project-local-vps"
        pipeline._set_heartbeat_context = lambda **kwargs: None

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "card_final.mp4"
            thumb_path = Path(tmpdir) / "card_final.png"
            video_path.write_bytes(b"video")
            thumb_path.write_bytes(b"thumb")

            env = self._base_live_vps_env()
            env["VPS_SSH_PATH"] = "relative/upload-dir"

            with patch.dict(os.environ, env, clear=True):
                with patch("pipeline.subprocess.run") as run_mock:
                    with self.assertRaises(PipelineStageError) as ctx:
                        WHOOPPipeline.step_12_upload_vps(pipeline, video_path, thumb_path)

        self.assertEqual(ctx.exception.stage, "VPS Upload")
        self.assertIn("VPS_SSH_PATH must be an absolute remote server path", ctx.exception.message)
        run_mock.assert_not_called()

    def test_dockerignore_excludes_env_file(self):
        dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", dockerignore)

    def test_dokploy_safety_check_accepts_expected_env_and_mounts(self):
        env = {
            "STATE_ZERO_PRIVATE_ROOT": "/opt/state-zero-private",
            "PIPELINE_MEDIA_MODE": "live_vps",
            "VPS_SSH_PATH": "/srv/state-zero-media",
        }
        container_spec = {
            "Env": [f"{key}={value}" for key, value in env.items()],
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": "/opt/state-zero-private",
                    "Target": "/opt/state-zero-private",
                    "ReadOnly": False,
                },
                {
                    "Type": "bind",
                    "Source": "/srv/state-zero-media",
                    "Target": "/srv/state-zero-media",
                    "ReadOnly": False,
                },
            ],
        }

        self.assertEqual(check_dokploy_deployment_safety.check_env(env), [])
        self.assertEqual(check_dokploy_deployment_safety.check_mounts(container_spec), [])

    def test_dokploy_safety_check_rejects_placeholder_media_path(self):
        env = {
            "STATE_ZERO_PRIVATE_ROOT": "/opt/state-zero-private",
            "PIPELINE_MEDIA_MODE": "live_vps",
            "VPS_SSH_PATH": "/srv/example-media",
        }

        findings = check_dokploy_deployment_safety.check_env(env)

        self.assertIn("VPS_SSH_PATH expected '/srv/state-zero-media'", findings[0])

    def test_dokploy_safety_check_rejects_missing_or_readonly_mounts(self):
        container_spec = {
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": "/opt/state-zero-private",
                    "Target": "/opt/state-zero-private",
                    "ReadOnly": True,
                },
            ],
        }

        findings = check_dokploy_deployment_safety.check_mounts(container_spec)

        self.assertIn("mount /opt/state-zero-private -> /opt/state-zero-private is read-only", findings)
        self.assertIn("missing bind mount /srv/state-zero-media -> /srv/state-zero-media", findings)

    def test_dokploy_safety_check_parses_missing_host_paths(self):
        output = "OK\t/opt/state-zero-private/runtime/output\nMISSING\t/srv/state-zero-media/fallback/error_404_v1/card.mp4\n"

        findings = check_dokploy_deployment_safety.parse_host_path_probe(output)

        self.assertEqual(
            findings,
            ["missing host path /srv/state-zero-media/fallback/error_404_v1/card.mp4"],
        )

    def test_dokploy_safety_check_separates_host_path_probe_blocks(self):
        command = check_dokploy_deployment_safety.build_host_path_probe_command(
            ("/opt/state-zero-private/runtime/output", "/srv/state-zero-media")
        )

        self.assertIn("; if [ -e /srv/state-zero-media ]", command)


if __name__ == "__main__":
    unittest.main()

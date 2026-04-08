"""Focused tests for video prompt materiality guardrails."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from prompts import PromptOrchestrator


def _build_orchestrator(tmpdir: str) -> PromptOrchestrator:
    with patch.dict(
        os.environ,
        {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-04-08"},
        clear=False,
    ):
        return PromptOrchestrator(llm_api_key="mock")


class MaterialClassMappingTests(unittest.TestCase):
    """Only environments needing soft-matter handling are explicitly mapped."""

    def test_atmospheric_environments(self):
        for env in ("Wind/Sky Realms", "Mist/Fog Realms", "Plasma/Nebula"):
            with self.subTest(env=env):
                self.assertEqual(
                    PromptOrchestrator.ENVIRONMENT_MATERIAL_CLASS[env],
                    "atmospheric",
                )

    def test_unlisted_environment_defaults_to_solid(self):
        self.assertEqual(
            PromptOrchestrator.ENVIRONMENT_MATERIAL_CLASS.get("Frozen/Ice", "solid"),
            "solid",
        )

    def test_fluid_environment(self):
        self.assertEqual(
            PromptOrchestrator.ENVIRONMENT_MATERIAL_CLASS["Ocean/Underwater"],
            "fluid",
        )

class MaterialityValidationTests(unittest.TestCase):
    """Sentence-2 guardrails for atmospheric/fluid scenes."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orc = _build_orchestrator(self._tmp)

    # --- atmospheric rejections ---

    def _wind_sky_fracture_prompt(self) -> str:
        return (
            "The camera holds absolutely still, fixed axis, witnessing without comment. "
            "A weather front fractures the cloud bank, debris scattering across the frame "
            "as the environment cracks at the surface under full open sky. "
            "Film grain throughout, lens bloom on the brightest edge."
        )

    def test_wind_sky_low_fracture_rejected(self):
        reasons = self._orc._validate_video_prompt(
            self._wind_sky_fracture_prompt(),
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="atmospheric",
        )
        materiality_reasons = [r for r in reasons if r.startswith("materiality_violation_atmospheric:")]
        self.assertTrue(
            len(materiality_reasons) >= 1,
            f"Expected materiality violation, got: {reasons}",
        )

    def test_wind_sky_low_fracture_reason_names_term(self):
        reasons = self._orc._validate_video_prompt(
            self._wind_sky_fracture_prompt(),
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="atmospheric",
        )
        materiality = [r for r in reasons if r.startswith("materiality_violation_atmospheric:")]
        self.assertTrue(materiality)
        term = materiality[0].split(":", 1)[1]
        self.assertIn(term, ("fractures", "fracture", "debris", "cracks", "crack"))

    def test_mist_fog_low_solid_rejected(self):
        prompt = (
            "The camera drifts inward on a slow fixed axis. "
            "Heavy saturated air shatters the fog layer, splinters of condensed vapor "
            "cascading downward as visibility collapses to near zero. "
            "Film grain throughout, lens bloom at the densest zone."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="atmospheric",
        )
        self.assertTrue(
            any(r.startswith("materiality_violation_atmospheric:") for r in reasons),
            f"Expected materiality violation, got: {reasons}",
        )

    def test_plasma_nebula_low_solid_rejected(self):
        prompt = (
            "The camera holds still, fixed witnessing frame. "
            "A pressure wave cracks the plasma core, debris of superheated gas "
            "fragmenting outward in visible ruptures across the nebula field. "
            "Film grain present throughout, lens bloom at the core."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="atmospheric",
        )
        self.assertTrue(
            any(r.startswith("materiality_violation_atmospheric:") for r in reasons),
            f"Expected materiality violation, got: {reasons}",
        )

    # --- atmospheric passes ---

    def test_wind_sky_low_weather_physics_passes(self):
        """Severe atmospheric failure using weather vocabulary should pass."""
        prompt = (
            "The camera holds absolutely still, the fixed frame witnessing the full weight "
            "of the overdriven sky without movement or retreat. "
            "A pressure front streams laterally through the cloud mass — directional bands "
            "of storm-thickened air compress the lower layer, the atmosphere shearing "
            "horizontally as visibility fails across the full frame for the full 8 seconds. "
            "Film grain throughout, lens bloom diffused through the compressed upper zone."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="atmospheric",
        )
        self.assertFalse(
            any(r.startswith("materiality_violation_atmospheric:") for r in reasons),
            f"Unexpected materiality violation: {reasons}",
        )

    def test_atmospheric_solid_subject_exemption(self):
        """Solid-failure term is allowed when a named solid formation is the explicit subject."""
        prompt = (
            "The camera holds still, fixed axis. "
            "A cliff face fractures along a mineral seam, "
            "dust cascading from the break point as pressure-driven wind drives past. "
            "Film grain throughout, lens bloom at the break edge."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="atmospheric",
        )
        self.assertFalse(
            any(r.startswith("materiality_violation_atmospheric:") for r in reasons),
            f"Solid subject should exempt the term: {reasons}",
        )

    def test_atmospheric_nearby_solid_noun_does_not_exempt_cloud_failure(self):
        """A nearby cliff/reef noun should not pardon clouds behaving like solids."""
        prompt = (
            "The camera holds still, fixed axis. "
            "The cloud bank shatters beside a cliff wall, debris scattering across the frame "
            "as the storm force tears through the open sky. "
            "Film grain throughout, lens bloom at the brightest edge."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="atmospheric",
        )
        self.assertTrue(
            any(r.startswith("materiality_violation_atmospheric:") for r in reasons),
            f"Nearby solid noun should not exempt cloud failure: {reasons}",
        )

    # --- solid environments not blocked ---

    def test_frozen_ice_low_fracture_allowed(self):
        prompt = (
            "The camera holds absolutely still, the fixed frame witnessing the fracture "
            "without comment or retreat. "
            "A crack propagates slowly across the compressed ice face — fine ice splinters "
            "fall from the fracture line and scatter across the surface below. "
            "Pressure seams catch cold internal light, film grain throughout, lens bloom minimal."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="ABYSS",
            recovery_zone="LOW",
            material_class="solid",
        )
        self.assertFalse(
            any(r.startswith("materiality_violation") for r in reasons),
            f"Solid environment should allow fracture language: {reasons}",
        )

    def test_glacial_valley_low_calving_allowed(self):
        prompt = (
            "The camera holds still on a level axis, fixed witness to the far edge. "
            "Ice calves at the far edge — a mass separates and drops with visible impact, "
            "the break sending a dense ice-dust cloud rolling across the valley floor. "
            "Film grain throughout, lens bloom diffused through the settling cloud."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="solid",
        )
        self.assertFalse(
            any(r.startswith("materiality_violation") for r in reasons),
            f"Solid environment should allow ice calving/debris: {reasons}",
        )

    # --- fluid environment ---

    def test_ocean_underwater_low_surge_passes(self):
        """Surge/sediment vocabulary for fluid LOW should pass."""
        prompt = (
            "The camera drifts inward on a slow fixed axis. "
            "A surge current blasts sediment from the base in a billowing cloud that expands "
            "and holds, formations obscured for the full 8 seconds as disturbed matter "
            "refuses to settle. "
            "Film grain throughout, lens bloom diffused through the suspended sediment."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="fluid",
        )
        self.assertFalse(
            any(r.startswith("materiality_violation_fluid:") for r in reasons),
            f"Surge vocabulary should pass for fluid: {reasons}",
        )

    def test_ocean_underwater_low_crack_rejected(self):
        """Crack/fracture language applied to water/sediment should be rejected."""
        prompt = (
            "The camera holds still. "
            "The water fractures under the surge, debris and shattered sediment cascading "
            "across the frame as the current cracks the ocean floor apart. "
            "Film grain throughout, lens bloom at the brightest zone."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="fluid",
        )
        self.assertTrue(
            any(r.startswith("materiality_violation_fluid:") for r in reasons),
            f"Expected fluid materiality violation, got: {reasons}",
        )

    def test_ocean_underwater_solid_reef_exemption(self):
        """Fluid environment: fracture is acceptable when a rock/reef is the named subject."""
        prompt = (
            "The camera drifts inward. "
            "A reef formation fractures along a pressure seam, fragments falling into the "
            "sediment cloud that billows outward across the frame. "
            "Film grain throughout, lens bloom at the brightest zone."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="fluid",
        )
        self.assertFalse(
            any(r.startswith("materiality_violation_fluid:") for r in reasons),
            f"Named reef formation should exempt solid-failure term: {reasons}",
        )

    def test_ocean_underwater_solid_reef_debris_still_allowed(self):
        """Debris later in the sentence should pass when a solid object is explicitly failing."""
        prompt = (
            "The camera drifts inward. "
            "A reef formation fractures along a pressure seam, debris scattering outward "
            "as the sediment cloud billows across the frame. "
            "Film grain throughout, lens bloom at the brightest zone."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="fluid",
        )
        self.assertFalse(
            any(r.startswith("materiality_violation_fluid:") for r in reasons),
            f"Named solid failure should allow later debris: {reasons}",
        )

    def test_fluid_nearby_reef_does_not_exempt_water_failure(self):
        """A nearby reef noun should not pardon water behaving like solid matter."""
        prompt = (
            "The camera drifts inward on a slow fixed axis. "
            "The water fractures around a reef formation, debris and shattered sediment "
            "cascading through the current as the surge thickens. "
            "Film grain throughout, lens bloom at the brightest zone."
        )
        reasons = self._orc._validate_video_prompt(
            prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="fluid",
        )
        self.assertTrue(
            any(r.startswith("materiality_violation_fluid:") for r in reasons),
            f"Nearby reef noun should not exempt water failure: {reasons}",
        )


class MaterialityRetryGuidanceTests(unittest.TestCase):
    """_build_video_retry_prompt explains atmospheric materiality violations clearly."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orc = _build_orchestrator(self._tmp)

    def test_atmospheric_retry_names_the_term(self):
        filled = "The template placeholder."
        previous = "A pressure wave fractures the cloud layer, debris scattering."
        retry = self._orc._build_video_retry_prompt(
            filled,
            previous_output=previous,
            rejection_reasons=["materiality_violation_atmospheric:fractures"],
        )
        self.assertIn("fractures", retry)

    def test_atmospheric_retry_explains_weather_physics(self):
        filled = "The template placeholder."
        previous = "The sky cracks open as debris rains down."
        retry = self._orc._build_video_retry_prompt(
            filled,
            previous_output=previous,
            rejection_reasons=["materiality_violation_atmospheric:crack"],
        )
        self.assertIn("pressure", retry.lower())
        self.assertIn("atmospheric", retry.lower())

    def test_atmospheric_retry_preserves_intensity_instruction(self):
        """Retry guidance must say intensity is still required, not to soften the scene."""
        filled = "The template placeholder."
        previous = "The sky shatters like glass."
        retry = self._orc._build_video_retry_prompt(
            filled,
            previous_output=previous,
            rejection_reasons=["materiality_violation_atmospheric:shatters"],
        )
        # Should mention keeping intensity, not softening
        self.assertIn("intensity", retry.lower())

    def test_fluid_retry_explains_fluid_physics(self):
        filled = "The template placeholder."
        previous = "The water fractures, debris scattering."
        retry = self._orc._build_video_retry_prompt(
            filled,
            previous_output=previous,
            rejection_reasons=["materiality_violation_fluid:fractures"],
        )
        self.assertIn("fluid", retry.lower())
        self.assertIn("fractures", retry)


class RegressionApril8Tests(unittest.TestCase):
    """Known-bad case from April 8: Wind/Sky Realms + LOW + SURFACE with solid language."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orc = _build_orchestrator(self._tmp)

    def test_known_bad_case_is_rejected(self):
        """The April 8 bad prompt uses 'brittle', 'rupturing', 'cracking at the surface'."""
        bad_prompt = (
            "The camera holds still, fixed axis, a geological witness to collapse. "
            "The environment is brittle and rupturing — the sky surface is cracking at the "
            "surface as the atmosphere fragments under its own weight, debris streaming "
            "across the open frame in sustained directional flow. "
            "Film grain throughout, lens bloom on the fractured horizon edge."
        )
        reasons = self._orc._validate_video_prompt(
            bad_prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="atmospheric",
        )
        self.assertTrue(
            any(r.startswith("materiality_violation_atmospheric:") for r in reasons),
            f"April 8 bad case should be rejected: {reasons}",
        )

    def test_corrected_case_passes(self):
        """A corrected Wind/Sky LOW prompt using weather physics should pass."""
        good_prompt = (
            "The camera holds absolutely still, the fixed frame holding the full weight "
            "of the overdriven sky without retreat. "
            "A pressure front accelerates through the cloud mass — storm-thickened air "
            "streams laterally in directional sheets as the lower layer fails under compression, "
            "visibility driven to near zero across the full frame for the entire 8 seconds. "
            "Film grain throughout, lens bloom diffused through the compressed upper zone."
        )
        reasons = self._orc._validate_video_prompt(
            good_prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="atmospheric",
        )
        self.assertFalse(
            any(r.startswith("materiality_violation_atmospheric:") for r in reasons),
            f"Corrected case should pass: {reasons}",
        )

    def test_corrected_case_still_has_failure_event(self):
        """The corrected case must still satisfy the LOW failure event check."""
        good_prompt = (
            "The camera holds absolutely still, the fixed frame holding the full weight "
            "of the overdriven sky without retreat. "
            "A pressure front accelerates through the cloud mass — storm-thickened air "
            "streams laterally in directional sheets as the lower layer fails under compression, "
            "visibility driven to near zero across the full frame for the entire 8 seconds. "
            "Film grain throughout, lens bloom diffused through the compressed upper zone."
        )
        reasons = self._orc._validate_video_prompt(
            good_prompt,
            depth_level="SURFACE",
            recovery_zone="LOW",
            material_class="atmospheric",
        )
        self.assertNotIn("low_recovery_sentence_two_no_failure_event", reasons)


if __name__ == "__main__":
    unittest.main()

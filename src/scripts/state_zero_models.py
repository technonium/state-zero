"""Application media identities; installed gflow keeps independently checked literals."""

FLOW_IMAGE_LABEL = 'Nano Banana 2.1'
FLOW_IMAGE_ALIAS = 'nano2'
FLOW_IMAGE_ENUM = 'NARWHAL'
FLOW_IMAGE_WIRE = 'BELUGA'
FLOW_VIDEO_LABEL = 'Omni 1.2 Flash'
FLOW_VIDEO_ALIAS = 'omni-1.2-flash'
FLOW_VIDEO_RESULT = 'omni_1_2_flash'
FLOW_VIDEO_ENUM = 'OMNI_1_2_FLASH'
GOOGLE_IMAGE_MODEL = 'gemini-nano-banana-2.1'
GOOGLE_VIDEO_MODEL = 'gemini-omni-1.2-flash'
GEMINI_MODEL = 'gemini-4-hydrogen'


def flow_video_model_matches(model: object) -> bool:
    # The patch owns this pure wire predicate; the CLI never imports app constants.
    from gflow_cli.api.video import is_omni12_i2v_model
    return is_omni12_i2v_model(model)

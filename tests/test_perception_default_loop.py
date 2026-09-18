from voidcube.systems.perception import (
    AdaptiveLocalVisionAnalyzer,
    LocalPerceptionLoop,
    OmniParserAdapter,
    build_default_local_perception_loop,
)


def test_default_loop_wires_mss_and_adaptive_local_analyzer() -> None:
    loop = build_default_local_perception_loop(monitor=1, capture_session_id="mvp")

    assert isinstance(loop, LocalPerceptionLoop)
    assert isinstance(loop.analyzer, AdaptiveLocalVisionAnalyzer)
    assert loop.capture_session_id == "mvp"


def test_default_loop_accepts_an_audited_screen_parser_backend() -> None:
    parser = OmniParserAdapter(lambda _image: {"visible_text": ["ok"]})
    loop = build_default_local_perception_loop(screen_parser=parser)

    assert loop.analyzer.screen_parser is parser

from whisper_transcriber.formatters import format_srt_ts, safe_slug, to_srt, to_vtt


def test_safe_slug_replaces_unsafe_characters_and_collapses_spaces():
    assert safe_slug("  Héllo / world!  ") == "Héllo___world"


def test_safe_slug_falls_back_to_audio_for_empty_result():
    assert safe_slug("!!!") == "audio"


def test_format_srt_ts_formats_hms_with_milliseconds():
    assert format_srt_ts(3661.789) == "01:01:01,789"


def test_format_srt_ts_clamps_negative_values_to_zero():
    assert format_srt_ts(-1.2) == "00:00:00,000"


def test_to_srt_renders_numbered_cues():
    segments = [
        {"start": 0, "end": 1.25, "text": "Hello"},
        {"start": 2, "end": 3.5, "text": "World"},
    ]

    assert (
        to_srt(segments)
        == "1\n00:00:00,000 --> 00:00:01,250\nHello\n\n"
        "2\n00:00:02,000 --> 00:00:03,500\nWorld\n"
    )


def test_to_srt_strips_cue_text():
    segments = [{"start": 0, "end": 1, "text": "  Hello world  "}]

    assert to_srt(segments) == "1\n00:00:00,000 --> 00:00:01,000\nHello world\n"


def test_to_vtt_renders_webvtt_header_and_period_timestamps():
    segments = [{"start": 0, "end": 1.25, "text": "Hello"}]

    assert to_vtt(segments) == "WEBVTT\n\n00:00:00.000 --> 00:00:01.250\nHello\n"


def test_to_vtt_strips_cue_text():
    segments = [{"start": 0, "end": 1, "text": "  Hello world  "}]

    assert to_vtt(segments) == "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello world\n"

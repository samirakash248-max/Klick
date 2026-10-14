"""Unit tests for Bidirectional Voice & Text-to-Speech (TTS) Engine."""

from pathlib import Path
import pytest

from backend.app.schemas import TTSSettingsRequest, TTSStatusResponse
from backend.app.voice_tts import VoiceTTSEngine


def test_voice_tts_status_and_discovery():
    """Verify voice synthesizer reports configuration and installed voices."""
    engine = VoiceTTSEngine()
    status = engine.get_status()

    assert isinstance(status, TTSStatusResponse)
    assert status.enabled is True
    assert status.engine == "sapi5_windows"
    assert status.rate == 175
    assert status.volume == 1.0
    assert len(status.available_voices) >= 1


def test_voice_tts_settings_update():
    """Verify updating TTS volume and speech rate."""
    engine = VoiceTTSEngine()
    engine.update_settings(TTSSettingsRequest(rate=200, volume=0.8, enabled=True))

    status = engine.get_status()
    assert status.rate == 200
    assert status.volume == 0.8
    assert status.enabled is True


def test_voice_tts_generate_wav():
    """Verify generating WAV audio bytes and base64 payloads."""
    engine = VoiceTTSEngine()
    wav_bytes = engine.generate_wav_bytes("Testing voice synthesis")

    assert wav_bytes is not None
    assert len(wav_bytes) > 1000
    # RIFF header check for valid WAV format
    assert wav_bytes[:4] == b"RIFF"

    wav_b64 = engine.generate_wav_base64("Testing voice base64 synthesis")
    assert wav_b64 is not None
    assert len(wav_b64) > 1000


def test_voice_tts_speak_enqueue():
    """Verify enqueuing speech does not raise exceptions."""
    engine = VoiceTTSEngine()
    spoken = engine.speak("Unit test speech dispatch", wait=False)
    assert spoken is True

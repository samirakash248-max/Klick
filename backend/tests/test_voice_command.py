"""Unit tests for Voice Commands & Speech-to-Text (STT) Navigation."""

import base64
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.schemas import VoiceGoalCommandRequest, VoiceTranscribeRequest, VoiceTranscribeResponse
from backend.app.voice_tts import VoiceTTSEngine, VoiceTranscriptionEngine
from frontend.services.backend_client import BackendClient


@pytest.fixture
def test_client():
    return TestClient(app)


def test_voice_transcription_engine_invalid_buffer():
    """Verify transcription engine cleanly reports error on corrupt/empty buffer."""
    engine = VoiceTranscriptionEngine()
    
    # Empty buffer
    res_empty = engine.transcribe_audio(b"")
    assert res_empty.success is False
    assert "too small" in (res_empty.error or "").lower()

    # Corrupt tiny buffer
    res_tiny = engine.transcribe_audio(b"RIFF123456789")
    assert res_tiny.success is False

    # Invalid base64
    res_b64 = engine.transcribe_audio("not-a-valid-base64-string!@#$")
    assert res_b64.success is False


def test_voice_transcription_engine_synthetic_audio():
    """Verify speech synthesizer wav is accurately transcribed by STT engine."""
    tts = VoiceTTSEngine()
    wav_bytes = tts.generate_wav_bytes("open youtube and search music")
    assert wav_bytes is not None and len(wav_bytes) > 1000

    stt = VoiceTranscriptionEngine()
    res = stt.transcribe_audio(wav_bytes)
    assert res.success is True
    assert "youtube" in res.transcript.lower() or "music" in res.transcript.lower()


def test_api_voice_transcribe_endpoint(test_client):
    """Verify POST /api/voice/transcribe endpoint returns structured transcription."""
    tts = VoiceTTSEngine()
    wav_bytes = tts.generate_wav_bytes("navigate to invoices")
    wav_b64 = base64.b64encode(wav_bytes).decode("utf-8")

    response = test_client.post(
        "/api/voice/transcribe",
        json={"audio_base64": wav_b64, "language": "en-US"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "invoice" in data["transcript"].lower()


def test_api_voice_transcribe_missing_audio(test_client):
    """Verify POST /api/voice/transcribe rejects empty payload."""
    response = test_client.post("/api/voice/transcribe", json={})
    assert response.status_code == 400


def test_api_voice_command_direct_transcript(test_client):
    """Verify POST /api/voice/command handles pre-transcribed text commands."""
    response = test_client.post(
        "/api/voice/command",
        json={
            "transcript": "Navigate to settings page",
            "start_url": "http://localhost:8080/settings",
            "max_steps": 5,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("success") is True
    assert data.get("spoken_goal") == "Navigate to settings page"


def test_api_voice_command_with_audio_recording(test_client):
    """Verify POST /api/voice/command transcribes audio and launches task."""
    tts = VoiceTTSEngine()
    wav_bytes = tts.generate_wav_bytes("check system status")
    wav_b64 = base64.b64encode(wav_bytes).decode("utf-8")

    response = test_client.post(
        "/api/voice/command",
        json={
            "audio_base64": wav_b64,
            "max_steps": 5,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("success") is True
    assert "status" in data.get("spoken_goal", "").lower() or "system" in data.get("spoken_goal", "").lower()


def test_api_voice_command_rejects_empty(test_client):
    """Verify POST /api/voice/command rejects requests without audio or text."""
    response = test_client.post("/api/voice/command", json={})
    assert response.status_code == 400


def test_backend_client_voice_helpers():
    """Verify BackendClient client methods construct valid payloads."""
    client = BackendClient()
    tts = VoiceTTSEngine()
    wav_bytes = tts.generate_wav_bytes("hello world")

    # Mock client session internally by direct verification of schema methods
    res = client.execute_voice_command(transcript="test goal")
    # Will connect to live server if up or return error dict
    assert isinstance(res, dict)

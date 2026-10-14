"""Bidirectional Voice & Text-to-Speech (TTS) Engine for Klick Browser Agent.

Allows Klick to speak concise real-time voice updates aloud during web navigation
and task completion using offline Windows SAPI5 / pyttsx3 speech synthesis,
and provides WAV audio synthesis for the Streamlit dashboard and Chrome Extension.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

from .schemas import TTSRequest, TTSSettingsRequest, TTSStatusResponse

logger = logging.getLogger(__name__)


class VoiceTTSEngine:
    """Thread-safe Text-to-Speech coordinator for voice feedback."""

    def __init__(self):
        self.enabled: bool = True
        self.rate: int = 175
        self.volume: float = 1.0
        self.current_voice_id: Optional[str] = None
        self._speech_queue: queue.Queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._engine_lock = threading.Lock()
        self._cached_voices: List[Dict[str, Any]] = []

        self._discover_voices()
        self._start_worker()

    def _discover_voices(self):
        """Enumerate available system voices."""
        try:
            import win32com.client
            sp = win32com.client.Dispatch("SAPI.SpVoice")
            voices = sp.GetVoices()
            res = []
            for i in range(voices.Count):
                v = voices.Item(i)
                res.append({
                    "id": v.Id,
                    "name": v.GetDescription(),
                    "gender": "female" if "zira" in v.GetDescription().lower() else "male",
                    "languages": ["en"],
                })
            self._cached_voices = res
            if res and not self.current_voice_id:
                self.current_voice_id = res[0]["id"]
        except Exception as e:
            logger.warning(f"Native voice discovery fallback: {e}")
            try:
                import pyttsx3
                engine = pyttsx3.init()
                voices = engine.getProperty("voices")
                res = []
                for v in voices:
                    res.append({
                        "id": getattr(v, "id", str(v)),
                        "name": getattr(v, "name", str(v)),
                        "gender": getattr(v, "gender", "unknown"),
                        "languages": getattr(v, "languages", ["en"]),
                    })
                self._cached_voices = res
                if res and not self.current_voice_id:
                    self.current_voice_id = res[0]["id"]
                engine.stop()
            except Exception:
                self._cached_voices = [{"id": "default", "name": "System Default Voice"}]

    def _start_worker(self):
        """Start background worker thread for fallback queue."""
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._speech_worker_loop, daemon=True)
        self._worker_thread.start()

    def _speech_worker_loop(self):
        """Background worker loop fallback."""
        while not self._stop_event.is_set():
            try:
                item = self._speech_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if not self.enabled:
                self._speech_queue.task_done()
                continue

            text, voice_id, rate, volume, done_event = item
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                sp = win32com.client.Dispatch("SAPI.SpVoice")
                if voice_id:
                    for v in sp.GetVoices():
                        if v.Id == voice_id:
                            sp.Voice = v
                            break
                sapi_rate = max(-10, min(10, int((rate - 175) / 15)))
                sp.Rate = sapi_rate
                sp.Volume = int(max(0.0, min(1.0, volume)) * 100)
                sp.Speak(text, 0)
                pythoncom.CoUninitialize()
            except Exception as e:
                logger.error(f"[TTS] Worker speech error: {e}")
            finally:
                if done_event:
                    done_event.set()
                self._speech_queue.task_done()

    def speak(
        self,
        text: str,
        wait: bool = False,
        voice_id: Optional[str] = None,
        rate: Optional[int] = None,
        volume: Optional[float] = None,
    ) -> bool:
        """Enqueue or speak text aloud immediately without blocking."""
        if not self.enabled or not text or not text.strip():
            return False

        clean_text = text.strip()
        target_voice = voice_id or self.current_voice_id
        target_rate = rate or self.rate
        target_vol = volume or self.volume

        try:
            import win32com.client
            sp = win32com.client.Dispatch("SAPI.SpVoice")
            if target_voice:
                for v in sp.GetVoices():
                    if v.Id == target_voice or target_voice in v.GetDescription():
                        sp.Voice = v
                        break
            sapi_rate = max(-10, min(10, int((target_rate - 175) / 15)))
            sp.Rate = sapi_rate
            sp.Volume = int(max(0.0, min(1.0, target_vol)) * 100)
            # SVSFlagsAsync = 1, SVSFDefault = 0
            flags = 0 if wait else 1
            sp.Speak(clean_text, flags)
            return True
        except Exception as e:
            logger.warning(f"[TTS] Direct SAPI speak fallback to queue: {e}")
            done_event = threading.Event() if wait else None
            self._speech_queue.put((clean_text, target_voice, target_rate, target_vol, done_event))
            if wait and done_event:
                done_event.wait(timeout=10.0)
            return True

    def generate_wav_bytes(
        self,
        text: str,
        voice_id: Optional[str] = None,
        rate: Optional[int] = None,
        volume: Optional[float] = None,
    ) -> Optional[bytes]:
        """Synthesize text into WAV audio bytes."""
        if not text or not text.strip():
            return None

        clean_text = text.strip()
        target_voice = voice_id or self.current_voice_id
        target_rate = rate or self.rate
        target_vol = volume or self.volume

        tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = Path(tmp_wav.name)
        tmp_wav.close()

        try:
            import win32com.client
            sp = win32com.client.Dispatch("SAPI.SpVoice")
            fs = win32com.client.Dispatch("SAPI.SpFileStream")
            if target_voice:
                for v in sp.GetVoices():
                    if v.Id == target_voice or target_voice in v.GetDescription():
                        sp.Voice = v
                        break
            sapi_rate = max(-10, min(10, int((target_rate - 175) / 15)))
            sp.Rate = sapi_rate
            sp.Volume = int(max(0.0, min(1.0, target_vol)) * 100)

            fs.Open(str(tmp_path), 3)  # 3 = SSFMCreateForWrite
            sp.AudioOutputStream = fs
            sp.Speak(clean_text, 0)
            fs.Close()
            sp.AudioOutputStream = None

            if tmp_path.exists() and tmp_path.stat().st_size > 0:
                audio_bytes = tmp_path.read_bytes()
                return audio_bytes
            return None
        except Exception as e:
            logger.warning(f"[TTS] Direct SAPI WAV generation fallback: {e}")
            try:
                import pyttsx3
                with self._engine_lock:
                    eng = pyttsx3.init()
                    if target_voice:
                        eng.setProperty("voice", target_voice)
                    eng.setProperty("rate", target_rate)
                    eng.setProperty("volume", target_vol)
                    eng.save_to_file(clean_text, str(tmp_path))
                    eng.runAndWait()
                    eng.stop()

                if tmp_path.exists() and tmp_path.stat().st_size > 0:
                    audio_bytes = tmp_path.read_bytes()
                    return audio_bytes
                return None
            except Exception as final_e:
                logger.error(f"[TTS] Failed generating WAV bytes: {final_e}")
                return None
        finally:
            tmp_path.unlink(missing_ok=True)

    def generate_wav_base64(
        self,
        text: str,
        voice_id: Optional[str] = None,
        rate: Optional[int] = None,
        volume: Optional[float] = None,
    ) -> Optional[str]:
        """Generate base64 encoded audio string for web clients."""
        raw_bytes = self.generate_wav_bytes(text, voice_id=voice_id, rate=rate, volume=volume)
        if raw_bytes:
            return base64.b64encode(raw_bytes).decode("utf-8")
        return None

    def get_status(self) -> TTSStatusResponse:
        """Return current status and configuration of TTS engine."""
        return TTSStatusResponse(
            enabled=self.enabled,
            engine="sapi5_windows",
            available_voices=self._cached_voices,
            current_voice=self.current_voice_id,
            rate=self.rate,
            volume=self.volume,
        )

    def update_settings(self, req: TTSSettingsRequest):
        """Update TTS parameters."""
        if req.enabled is not None:
            self.enabled = req.enabled
        if req.voice_id is not None:
            self.current_voice_id = req.voice_id
        if req.rate is not None:
            self.rate = req.rate
        if req.volume is not None:
            self.volume = max(0.0, min(1.0, req.volume))


# Singleton TTS engine instance
voice_tts_engine = VoiceTTSEngine()


# =========================================================================
# SPEECH-TO-TEXT (STT) TRANSCRIPTION ENGINE
# =========================================================================

class VoiceTranscriptionEngine:
    """Speech-to-Text transcription engine converting voice audio into action goals."""

    def __init__(self):
        self._recognizer = None

    def _get_recognizer(self):
        if self._recognizer is None:
            import speech_recognition as sr
            self._recognizer = sr.Recognizer()
        return self._recognizer

    def transcribe_audio(
        self,
        audio_content: Any,
        language: str = "en-US",
    ) -> Any:
        """Transcribe audio bytes, file path, or base64 data into text."""
        import io
        import speech_recognition as sr
        from .schemas import VoiceTranscribeResponse

        if isinstance(audio_content, str):
            p = Path(audio_content)
            if p.exists() and p.is_file():
                raw_bytes = p.read_bytes()
            else:
                cleaned = audio_content.strip()
                if "," in cleaned:
                    cleaned = cleaned.split(",", 1)[1]
                try:
                    raw_bytes = base64.b64decode(cleaned)
                except Exception as e:
                    return VoiceTranscribeResponse(
                        success=False,
                        transcript="",
                        error=f"Invalid base64 audio payload: {e}",
                    )
        else:
            raw_bytes = audio_content

        if not raw_bytes or len(raw_bytes) < 44:
            return VoiceTranscribeResponse(
                success=False,
                transcript="",
                error="Audio buffer too small or empty",
            )

        recognizer = self._get_recognizer()

        # Handle standard WAV audio file
        try:
            with io.BytesIO(raw_bytes) as audio_file:
                with sr.AudioFile(audio_file) as source:
                    audio_data = recognizer.record(source)
                    text = recognizer.recognize_google(audio_data, language=language)
                    return VoiceTranscribeResponse(
                        success=True,
                        transcript=text.strip(),
                        detected_language=language,
                    )
        except sr.UnknownValueError:
            return VoiceTranscribeResponse(
                success=False,
                transcript="",
                error="Could not understand audio (speech unclear or silence)",
            )
        except sr.RequestError as e:
            return VoiceTranscribeResponse(
                success=False,
                transcript="",
                error=f"Speech recognition service unavailable: {e}",
            )
        except Exception as exc:
            # Fallback if audio was WebM/Ogg/file: attempt temporary file write
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(raw_bytes)
                    tmp_name = tmp.name
                with sr.AudioFile(tmp_name) as source:
                    audio_data = recognizer.record(source)
                    text = recognizer.recognize_google(audio_data, language=language)
                    Path(tmp_name).unlink(missing_ok=True)
                    return VoiceTranscribeResponse(
                        success=True,
                        transcript=text.strip(),
                        detected_language=language,
                    )
            except Exception as final_err:
                return VoiceTranscribeResponse(
                    success=False,
                    transcript="",
                    error=f"Failed to process audio format: {final_err}",
                )


# Singleton voice transcription engine instance
voice_transcription_engine = VoiceTranscriptionEngine()


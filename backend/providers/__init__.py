"""VocalTwist provider registry — plug-in new STT/TTS providers here."""
from .base import STTProvider, TTSProvider
from .whisper_provider import WhisperSTTProvider
from .edge_tts_provider import EdgeTTSProvider

__all__ = ["STTProvider", "TTSProvider", "WhisperSTTProvider", "EdgeTTSProvider"]

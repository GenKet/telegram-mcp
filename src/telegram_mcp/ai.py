"""OpenAI integration: GPT text generation, TTS voice synthesis, Whisper transcription."""

import os
import tempfile
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI


class AIHelper:
    """Wrapper around OpenAI API for text generation, TTS, and transcription."""

    DEFAULT_CHAT_MODEL = "gpt-4o-mini"
    DEFAULT_TTS_MODEL = "tts-1"
    DEFAULT_TTS_VOICE = "alloy"
    DEFAULT_WHISPER_MODEL = "whisper-1"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise Exception(
                "OPENAI_API_KEY is not set. Add it to your .env file to use AI features."
            )
        self._client = AsyncOpenAI(api_key=self.api_key)

    async def generate_text(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        """Generate text via GPT from a prompt."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await self._client.chat.completions.create(
            model=model or self.DEFAULT_CHAT_MODEL,
            messages=messages,
        )
        return response.choices[0].message.content or ""

    async def generate_voice(
        self,
        text: str,
        voice: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        """Generate voice audio via TTS. Returns path to a temporary .ogg file."""
        tmp_path = Path(tempfile.gettempdir()) / f"tg_mcp_tts_{os.getpid()}_{id(text)}.ogg"

        response = await self._client.audio.speech.create(
            model=model or self.DEFAULT_TTS_MODEL,
            voice=voice or self.DEFAULT_TTS_VOICE,
            input=text,
            response_format="opus",
        )
        response.write_to_file(str(tmp_path))
        return str(tmp_path)

    async def transcribe_audio(
        self,
        file_path: str,
        language: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        """Transcribe audio file via Whisper."""
        with open(file_path, "rb") as f:
            kwargs = {
                "model": model or self.DEFAULT_WHISPER_MODEL,
                "file": f,
            }
            if language:
                kwargs["language"] = language
            result = await self._client.audio.transcriptions.create(**kwargs)
        return result.text

"""
Voice transcription using OpenAI Whisper.
Tries local whisper CLI first, falls back to OpenAI API.
"""
import asyncio
import os
import shutil
import tempfile

# Check for local whisper CLI
_WHISPER_CLI = shutil.which("whisper")


async def transcribe_voice(file_path: str) -> str:
    """Transcribe voice note using Gemini (primary). Local whisper CLI as fallback if available."""
    if _WHISPER_CLI:
        try:
            return await _transcribe_local(file_path)
        except Exception:
            pass
    return await _transcribe_gemini(file_path)


async def _transcribe_local(file_path: str) -> str:
    """Transcribe using local whisper CLI."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # whisper outputs to current directory by default
        cmd = [
            _WHISPER_CLI,
            file_path,
            "--model", "base",
            "--output_format", "txt",
            "--output_dir", tmpdir,
            "--language", "en",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"Whisper failed: {stderr.decode()[:200]}")

        # Find output file
        base = os.path.splitext(os.path.basename(file_path))[0]
        txt_path = os.path.join(tmpdir, f"{base}.txt")

        if os.path.exists(txt_path):
            with open(txt_path, "r") as f:
                return f.read().strip()

        # If no txt file, try to parse stdout
        return stdout.decode().strip() or "Transcription unavailable"


async def _transcribe_openai(file_path: str) -> str:
    """Transcribe using OpenAI Whisper API."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise RuntimeError("OpenAI package not installed and local whisper not available")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set and local whisper not available")

    client = AsyncOpenAI(api_key=api_key)

    with open(file_path, "rb") as audio_file:
        response = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="en",
        )

    return response.text.strip()

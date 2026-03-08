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


async def _transcribe_gemini(file_path: str) -> str:
    """Transcribe using Gemini's native audio understanding."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")

    client = genai.Client(api_key=api_key)

    with open(file_path, "rb") as f:
        audio_data = f.read()

    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {".ogg": "audio/ogg", ".mp3": "audio/mp3", ".wav": "audio/wav",
                ".m4a": "audio/mp4", ".webm": "audio/webm"}
    mime_type = mime_map.get(ext, "audio/ogg")

    loop = asyncio.get_event_loop()
    models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash"]
    contents = [
        "Transcribe this voice note exactly as spoken. Return only the transcribed text, no commentary.",
        types.Part.from_bytes(data=audio_data, mime_type=mime_type),
    ]

    last_error = None
    for model in models_to_try:
        try:
            response = await loop.run_in_executor(
                None,
                lambda m=model: client.models.generate_content(model=m, contents=contents)
            )
            return response.text.strip()
        except Exception as e:
            error_msg = str(e).lower()
            if any(t in error_msg for t in ["503", "unavailable", "overloaded", "404"]):
                last_error = e
                logger.warning(f"Whisper model {model} failed: {e}")
                continue
            raise
    raise last_error


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

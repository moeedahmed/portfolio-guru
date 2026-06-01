import asyncio
import os

import pytest

import whisper


class _FakeProcess:
    returncode = 0

    async def communicate(self):
        return b"", b""


@pytest.mark.asyncio
async def test_local_transcription_prefers_mlx_whisper_small(monkeypatch, tmp_path):
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"audio")
    captured_cmd = None

    async def fake_create_subprocess_exec(*cmd, **_kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        output_dir = captured_cmd[captured_cmd.index("--output-dir") + 1]
        with open(os.path.join(output_dir, "voice.txt"), "w") as f:
            f.write("portfolio guru transcription")
        return _FakeProcess()

    monkeypatch.setattr(whisper, "_MLX_WHISPER_CLI", "/opt/homebrew/bin/mlx_whisper")
    monkeypatch.setattr(whisper, "_WHISPER_CLI", "/Users/moeedahmed/.local/bin/whisper")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await whisper._transcribe_local(str(audio_path))

    assert result == "portfolio guru transcription"
    assert captured_cmd[:2] == ["/opt/homebrew/bin/mlx_whisper", str(audio_path)]
    assert "--model" in captured_cmd
    assert captured_cmd[captured_cmd.index("--model") + 1] == "mlx-community/whisper-small-mlx"
    assert "--output-format" in captured_cmd
    assert "base" not in captured_cmd


@pytest.mark.asyncio
async def test_local_transcription_falls_back_to_classic_whisper_base(monkeypatch, tmp_path):
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"audio")
    captured_cmd = None

    async def fake_create_subprocess_exec(*cmd, **_kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        output_dir = captured_cmd[captured_cmd.index("--output_dir") + 1]
        with open(os.path.join(output_dir, "voice.txt"), "w") as f:
            f.write("classic fallback transcription")
        return _FakeProcess()

    monkeypatch.setattr(whisper, "_MLX_WHISPER_CLI", None)
    monkeypatch.setattr(whisper, "_WHISPER_CLI", "/Users/moeedahmed/.local/bin/whisper")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await whisper._transcribe_local(str(audio_path))

    assert result == "classic fallback transcription"
    assert captured_cmd[:2] == ["/Users/moeedahmed/.local/bin/whisper", str(audio_path)]
    assert captured_cmd[captured_cmd.index("--model") + 1] == "base"

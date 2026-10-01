"""Mac voice I/O: mic capture -> whisper STT, and TTS via `say`.

Built-in macOS only. No pip deps beyond openai-whisper (already in venv).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")


class MicUnavailableError(RuntimeError):
    """Raised when the microphone cannot capture audio.

    Distinct from a transcription failure so callers can degrade
    gracefully (empty answer, keep going) instead of crashing the quiz.
    """

_whisper_model = None


def _ffmpeg() -> str:
    f = shutil.which("ffmpeg")
    if not f:
        raise RuntimeError("ffmpeg not found (brew install ffmpeg)")
    return f


def _audio_energy(wav_path: str | Path) -> float:
    """Peak absolute sample value of a wav. 0.0 = total silence."""
    import struct
    import wave
    with wave.open(str(wav_path), "rb") as w:
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2 or not raw:
        return 0.0
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    return max((abs(s) for s in samples), default=0.0)


def record(seconds: float, out_path: str | Path, mic_index: str = "0") -> str:
    """Record audio from the built-in mic via ffmpeg AVFoundation.

    Returns path to the wav file. Raises on failure - including when the
    capture produced total silence, which happens when ffmpeg has no audio
    input device (sandbox/headless) or the mic is muted. A silent file would
    otherwise be handed to whisper as a valid-but-empty recording.
    """
    out_path = str(out_path)
    cmd = [
        _ffmpeg(), "-y",
        "-f", "avfoundation",
        "-i", f":{mic_index}",  
        "-t", str(seconds),
        "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        # ffmpeg prints a lot to stderr; surface the decisive tail
        tail = (r.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError(f"ffmpeg record failed ({r.returncode}): " + " | ".join(tail))
    if _audio_energy(out_path) == 0.0:
        raise MicUnavailableError(
            "microphone captured total silence - no audio input device "
            "available here (sandbox/headless, or mic muted)."
        )
    return out_path


def _model():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        _whisper_model = whisper.load_model(WHISPER_MODEL)
    return _whisper_model


def transcribe(wav_path: str | Path, language: str | None = None) -> str:
    """Transcribe a wav file with whisper. Returns text."""
    m = _model()
    result = m.transcribe(str(wav_path), language=language, fp16=False)
    return (result.get("text") or "").strip()


def stream_answer(seconds: float = 8.0, chunk: float = 2.5, language: str | None = None) -> str:
    """Record from the mic and stream decoded text to stdout as it is spoken.

    No stdin. Records in short windows, transcribes each window immediately,
    and prints the decoded text as it arrives - so the developer sees their
    words appear while they are still talking. Returns the full transcript.

    Raises RuntimeError if the mic captures total silence (no audio input
    device here, or mic muted).
    """
    total = 0.0
    transcript: list[str] = []
    try:
        with tempfile.TemporaryDirectory() as td:
            while total < seconds:
                remaining = seconds - total
                if remaining <= 0:
                    break
                win = min(chunk, remaining)
                wav = Path(td) / f"chunk_{len(transcript)}.wav"
                record(win, wav)
                total += win
                text = transcribe(wav, language=language)
                if text:
                    transcript.append(text)
                    # Stream the decoded text live, no newline so the next
                    # chunk continues on the same line.
                    print(text, end=" ", flush=True)
            print()  # finish the streaming line
    except MicUnavailableError as e:
        # No audio input device here (sandbox/headless, or mic muted).
        # Print once and return an empty answer instead of crashing the
        # quiz flow - the developer can still complete the quiz on a
        # machine that has a working microphone.
        print(f"  [mic unavailable: {e}]", flush=True)
        return ""
    full = " ".join(transcript).strip()
    if not full:
        raise RuntimeError("no speech detected in the recording")
    return full


def speak(text: str, voice: str | None = None) -> None:
    """Speak text aloud using macOS `say`. Fire-and-forget."""
    cmd = ["say"]
    if voice:
        cmd += ["-v", voice]
    cmd.append(text)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def speak_blocking(text: str, voice: str | None = None) -> None:
    """Speak text aloud using macOS `say` and BLOCK until finished.

    Needed before recording: the developer must hear the whole question
    before the mic starts, otherwise they answer a question they have not
    heard yet.
    """
    cmd = ["say"]
    if voice:
        cmd += ["-v", voice]
    cmd.append(text)
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def voice_answer(seconds: float = 8.0, language: str | None = None) -> str:
    """Record the developer's spoken answer, transcribe it, return text.

    Streaming wrapper: prints decoded text as it is spoken, then returns the
    full transcript. Raises on total silence.
    """
    return stream_answer(seconds=seconds, language=language)


if __name__ == "__main__":
    print("Please speak your answer after the prompt.")
    answer = voice_answer()
    print("You said:", answer)

import sys
import time

try:
    import select
    import tty
    import termios
    UNIX_TTY = True
except ImportError:
    UNIX_TTY = False

# Try to import voice capabilities from mic.py (from Scrutiny)
try:
    from . import mic as _mic
    VOICE_AVAILABLE = True
except Exception:
    VOICE_AVAILABLE = False


class Interaction:
    def timed_input(self, prompt: str, timeout: int, speak_text: str = "") -> str:
        """Read a line from stdin with a live countdown timer.
        
        speak_text: the clean question text to speak aloud (no ANSI codes).
        On Mac/Linux: first tries to speak the question and record a voice
        answer via Whisper. Falls back to typed input if mic/Whisper is
        unavailable.
        On Windows: typed input with a live countdown timer via msvcrt.
        """
        if not sys.stdin.isatty():
            try:
                con = 'CON' if sys.platform == 'win32' else '/dev/tty'
                sys.stdin = open(con, "r")
            except Exception:
                ans = "Non-interactive mock answer"
                print(f"{prompt}{ans}")
                return ans

        # Try voice answer on Mac/Linux
        if UNIX_TTY and VOICE_AVAILABLE and speak_text:
            voice_ans = self._try_voice_answer(speak_text, timeout)
            if voice_ans is not None:
                return voice_ans
            # Voice unavailable or failed — fall through to typed input

        if not UNIX_TTY:
            return self._timed_input_windows(prompt, timeout)
        else:
            return self._timed_input_unix(prompt, timeout)

    def _try_voice_answer(self, question_text: str, timeout: int) -> str | None:
        """Speak the question and capture a spoken answer via Whisper (pyaudio backend).
        
        Uses pyaudio for recording (better macOS permission handling than avfoundation).
        Returns the transcribed text, or None to fall back to typed input.
        """
        import subprocess as _sp

        # Step 1: Always speak the question — this must never be silently swallowed
        try:
            _sp.run(["say", question_text], check=False,
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        except Exception as e:
            print(f"  [🔇 speak failed: {e}]", flush=True)

        # Step 2: Try microphone recording
        return self._record_and_transcribe(timeout)

    def _record_and_transcribe(self, timeout: int) -> str | None:
        """Record mic input via pyaudio and transcribe with Whisper.
        
        Returns transcribed text, or None on any failure (triggers typed fallback).
        """
        try:
            import pyaudio
            import wave
            import tempfile
            import whisper

            record_secs = min(timeout, 30)
            print(f"\n🎙️  Listening... (speak now, up to {record_secs}s)\n", flush=True)

            FORMAT = pyaudio.paInt16
            CHANNELS = 1
            RATE = 16000
            CHUNK = 1024

            audio = pyaudio.PyAudio()
            try:
                stream = audio.open(format=FORMAT, channels=CHANNELS,
                                    rate=RATE, input=True,
                                    frames_per_buffer=CHUNK)
            except Exception as e:
                audio.terminate()
                print(f"  [🎙️  Mic error: {e} — switching to typed input]", flush=True)
                return None

            frames = []
            for _ in range(int(RATE / CHUNK * record_secs)):
                frames.append(stream.read(CHUNK, exception_on_overflow=False))
            stream.stop_stream()
            stream.close()
            audio.terminate()

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                wav_path = tf.name
            with wave.open(wav_path, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(pyaudio.get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wf.writeframes(b''.join(frames))

            print("  [⚙️  Transcribing...]", flush=True)
            model = whisper.load_model("base")
            result = model.transcribe(wav_path, fp16=False)
            answer = (result.get("text") or "").strip()

            if answer:
                print(f"\n 🗣️  You said: {answer}\n", flush=True)
                return answer
            print("  [🎙️  No speech detected — switching to typed input]", flush=True)
            return None

        except ImportError as e:
            print(f"  [🎙️  Missing dependency ({e}) — switching to typed input]", flush=True)
            return None
        except Exception as e:
            print(f"  [🎙️  Recording failed: {e} — switching to typed input]", flush=True)
            return None

    def _timed_input_windows(self, prompt: str, timeout: int) -> str:
        import msvcrt
        start_time = time.time()
        user_input = []
        
        while True:
            remaining = int(timeout - (time.time() - start_time))
            if remaining <= 0:
                sys.stdout.write(f"\r\033[2K⏰ Time's up! ({timeout}s limit reached)\n")
                sys.stdout.flush()
                return None
                
            timer_str = f"⏱  {remaining:2d}s"
            current_str = "".join(user_input)
            display_str = ("..." + current_str[-50:]) if len(current_str) > 50 else current_str
            sys.stdout.write(f"\r{timer_str} | {prompt}{display_str} ")
            sys.stdout.flush()
            
            end_wait = time.time() + 0.2
            got_char = False
            while time.time() < end_wait:
                if msvcrt.kbhit():
                    got_char = True
                    break
                time.sleep(0.05)
                
            if not got_char:
                continue
                
            while msvcrt.kbhit():
                try:
                    ch = msvcrt.getwche()
                except Exception:
                    ch = msvcrt.getche().decode('utf-8', 'ignore')
                    
                if ch in ('\r', '\n'):
                    final_ans = "".join(user_input)
                    sys.stdout.write(f"\r\033[2K{prompt}{final_ans}\n")
                    sys.stdout.flush()
                    return final_ans
                elif ch == '\x08': # backspace
                    if user_input:
                        user_input.pop()
                elif ch == '\x03': # ctrl+c
                    raise KeyboardInterrupt()
                else:
                    user_input.append(ch)

    def _timed_input_unix(self, prompt: str, timeout: int) -> str:

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        
        start_time = time.time()
        user_input = []
        
        try:
            tty.setcbreak(fd)
            while True:
                remaining = int(timeout - (time.time() - start_time))
                if remaining <= 0:
                    sys.stdout.write(f"\r\033[2K⏰ Time's up! ({timeout}s limit reached)\n")
                    sys.stdout.flush()
                    return None
                    
                timer_str = f"⏱  {remaining:2d}s"
                current_str = "".join(user_input)
                # Truncate to prevent line wrapping which breaks \r\033[2K
                display_str = ("..." + current_str[-50:]) if len(current_str) > 50 else current_str
                sys.stdout.write(f"\r\033[2K{timer_str} | {prompt}{display_str}")
                sys.stdout.flush()
                
                # Wait up to 0.2s for any input
                ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                if not ready:
                    continue

                # Drain ALL immediately available characters (fixes paste flooding)
                submitted = False
                while True:
                    ch = sys.stdin.read(1)
                    if ch in ('\n', '\r'):
                        # Distinguish manual Enter vs multi-line paste chunk
                        more_now, _, _ = select.select([sys.stdin], [], [], 0.05)
                        if more_now:
                            user_input.append(' ')
                            continue
                        else:
                            submitted = True
                            break
                    elif ch in ('\x08', '\x7f'):
                        if user_input:
                            user_input.pop()
                    elif ch == '\x03':
                        raise KeyboardInterrupt()
                    elif ch == '\x04':
                        submitted = True
                        break
                    elif ch == '\x1b':
                        r, _, _ = select.select([sys.stdin], [], [], 0.05)
                        if r:
                            sys.stdin.read(1)
                            r2, _, _ = select.select([sys.stdin], [], [], 0.05)
                            if r2:
                                sys.stdin.read(1)
                    elif ch.isprintable():
                        user_input.append(ch)

                    # Check if more chars are immediately ready (paste burst)
                    more, _, _ = select.select([sys.stdin], [], [], 0.0)
                    if not more:
                        break  # No more chars — redraw timer once

                if submitted:
                    final_ans = "".join(user_input)
                    # Clear the timer line and print the FULL input so it remains on screen
                    sys.stdout.write(f"\r\033[2K{prompt}{final_ans}\n")
                    sys.stdout.flush()
                    return final_ans
        finally:
            termios.tcflush(fd, termios.TCIFLUSH)
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def print_context(self, context: dict):
        print("\n          CODE UNDERSTANDING CHECK\n")
        for f in context.get("structured_changes", []):
            print(f"FILE: {f['file']} | FUNCTION: {f['function']}() ")
            summary = f.get('summary', {})
            print("\n[SUMMARY]")
            print(f"What Changed: {summary.get('what_changed', 'N/A')}")
            print(f"Impact: {summary.get('impact', 'N/A')}")
            print(f"Why it matters: {summary.get('why_it_matters', 'N/A')}")
            print("\n[DIFF]")
            diff_lines = f['diff'].split('\n')
            if len(diff_lines) > 20:
                print("\n".join(diff_lines[:20]))
                print("... (diff truncated)")
            else:
                print(f['diff'])
            print("\n[CONTEXT / DEPENDENCIES]")
            print(f['dependency_summary'])
            print("\n")

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
    def __init__(self):
        self._whisper_model = None

    def timed_input(self, prompt: str, timeout: int, speak_text: str = "", seed_text: str = "") -> str:
        """Read a line from stdin with a live countdown timer.
        
        speak_text: the clean question text to speak aloud (no ANSI codes).
        1. Speaks the question aloud via macOS `say`.
        2. Displays the typing space immediately with [Tab] Mic option.
        3. User can type answer directly, or press [Tab] to trigger listening flow.
        4. If [Tab] is pressed, transcribed speech is placed into the typing space
           so the developer can review and edit before submitting with Enter.
        """
        if not sys.stdin.isatty():
            try:
                con = 'CON' if sys.platform == 'win32' else '/dev/tty'
                sys.stdin = open(con, "r")
            except Exception:
                ans = "Non-interactive mock answer"
                print(f"{prompt}{ans}")
                return ans

        # Step 1: Speak the question aloud first
        if speak_text:
            self._speak_question(speak_text)

        # Step 2: Show mic option hint if voice is available
        allow_mic = bool(VOICE_AVAILABLE and speak_text)
        if allow_mic:
            CYAN = "\033[96m"
            BOLD = "\033[1m"
            DIM = "\033[2m"
            RESET = "\033[0m"
            print(f"🎙️  {CYAN}{BOLD}[Tab]{RESET} {DIM}Speak with Mic  │  ⌨️  Type answer directly (Enter to submit){RESET}\n", flush=True)

        if not UNIX_TTY:
            return self._timed_input_windows(prompt, timeout, seed_text=seed_text, allow_mic=allow_mic)
        else:
            return self._timed_input_unix(prompt, timeout, seed_text=seed_text, allow_mic=allow_mic)

    def _speak_question(self, question_text: str):
        """Speak the question aloud using macOS say, blocking until finished."""
        if not question_text:
            return
        if sys.platform == 'darwin':
            try:
                import subprocess as _sp
                _sp.run(["say", question_text], check=False,
                        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            except Exception as e:
                print(f"  [🔇 speak failed: {e}]", flush=True)
            if UNIX_TTY:
                try:
                    termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
                except Exception:
                    pass

    def _record_and_transcribe(self, timeout: int) -> str | None:
        """Record mic input in short chunks and stream words to screen as spoken.
        
        Press Enter at any time to stop recording early and submit.
        Records in CHUNK_SECS windows, transcribes each chunk with Whisper
        immediately, and prints text as it arrives.
        Returns the full accumulated transcript, or None on failure.
        """
        try:
            import pyaudio
            import numpy as np
            import whisper

            CHUNK_SECS = 3
            RATE = 16000
            CHUNK = 1024
            FORMAT = pyaudio.paInt16
            CHANNELS = 1
            record_secs = min(timeout, 30)

            print(f"\n🎙️  Listening... (speak now — words appear as you speak, press Enter when done)\n", flush=True)
            sys.stdout.write(" 🗣️  ")
            sys.stdout.flush()

            if self._whisper_model is None:
                self._whisper_model = whisper.load_model("base")
            model = self._whisper_model

            audio_pa = pyaudio.PyAudio()
            try:
                stream = audio_pa.open(format=FORMAT, channels=CHANNELS,
                                       rate=RATE, input=True,
                                       frames_per_buffer=CHUNK)
            except Exception as e:
                audio_pa.terminate()
                print(f"\n  [🎙️  Mic error: {e} — switching to typed input]", flush=True)
                return None

            transcript_parts = []
            elapsed = 0.0
            frames_per_chunk = int(RATE / CHUNK * CHUNK_SECS)
            enter_pressed = False

            # Put stdin in cbreak so Enter is detectable between chunks
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                while elapsed < record_secs and not enter_pressed:
                    remaining = record_secs - elapsed
                    frames_this_chunk = min(frames_per_chunk, int(RATE / CHUNK * remaining))
                    if frames_this_chunk <= 0:
                        break

                    chunk_frames = []
                    for _ in range(frames_this_chunk):
                        # Check for Enter key before each frame read
                        r, _, _ = select.select([sys.stdin], [], [], 0)
                        if r:
                            key = sys.stdin.read(1)
                            if key in ('\r', '\n', '\x04'):
                                enter_pressed = True
                                break
                        chunk_frames.append(stream.read(CHUNK, exception_on_overflow=False))

                    if not chunk_frames:
                        break

                    elapsed += len(chunk_frames) * CHUNK / RATE

                    # Transcribe chunk as numpy float32 — no ffmpeg needed
                    raw = b''.join(chunk_frames)
                    if raw:
                        audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                        if len(audio_np) < 1600:
                            audio_np = np.pad(audio_np, (0, 1600 - len(audio_np)))
                        result = model.transcribe(audio_np, fp16=False)
                        text = (result.get("text") or "").strip()
                        if text:
                            sys.stdout.write(text + " ")
                            sys.stdout.flush()
                            transcript_parts.append(text)

                    if enter_pressed:
                        break

            finally:
                try:
                    termios.tcflush(fd, termios.TCIFLUSH)
                except Exception:
                    pass
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
                try:
                    audio_pa.terminate()
                except Exception:
                    pass

            print("\n", flush=True)
            full_answer = " ".join(transcript_parts).strip()

            if full_answer:
                return full_answer

            print("  [🎙️  No speech detected — switching to typed input]", flush=True)
            return None

        except ImportError as e:
            print(f"  [🎙️  Missing dependency ({e}) — switching to typed input]", flush=True)
            return None
        except Exception as e:
            print(f"  [🎙️  Recording failed: {e} — switching to typed input]", flush=True)
            return None

    def _timed_input_windows(self, prompt: str, timeout: int, seed_text: str = "", allow_mic: bool = False) -> str:
        import msvcrt
        start_time = time.time()
        user_input = list(seed_text) if seed_text else []
        
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
                    curr_text = "".join(user_input).strip().lower()
                    if allow_mic and curr_text in ("/mic", ":mic", "mic"):
                        user_input.clear()
                        ch = '\t'
                    else:
                        final_ans = "".join(user_input)
                        sys.stdout.write(f"\r\033[2K{prompt}{final_ans}\n")
                        sys.stdout.flush()
                        return final_ans

                if ch == '\t' and allow_mic:
                    sys.stdout.write(f"\r\033[2K")
                    sys.stdout.flush()
                    elapsed_so_far = int(time.time() - start_time)
                    voice_time = max(10, min(30, timeout - elapsed_so_far))
                    voice_ans = self._record_and_transcribe(voice_time)
                    if voice_ans:
                        if user_input and not user_input[-1].isspace():
                            user_input.append(' ')
                        user_input.extend(list(voice_ans))
                        print("\n✏️   Review and edit your answer below (Press Enter to submit):\n", flush=True)
                    else:
                        print("\n⌨️   Type your answer below (Press Enter to submit):\n", flush=True)
                    spent = int(time.time() - start_time)
                    if timeout - spent < 20:
                        start_time = time.time() - (timeout - 20)
                    break
                elif ch == '\x08': # backspace
                    if user_input:
                        user_input.pop()
                elif ch == '\x03': # ctrl+c
                    raise KeyboardInterrupt()
                elif ch.isprintable():
                    user_input.append(ch)

    def _timed_input_unix(self, prompt: str, timeout: int, seed_text: str = "", allow_mic: bool = False) -> str:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        
        start_time = time.time()
        user_input = list(seed_text) if seed_text else []
        
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
                            curr_text = "".join(user_input).strip().lower()
                            if allow_mic and curr_text in ("/mic", ":mic", "mic"):
                                user_input.clear()
                                ch = '\t'  # route to mic trigger below
                            else:
                                submitted = True
                                break

                    if ch == '\t' and allow_mic:
                        # Restore terminal temporarily for voice flow
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                        sys.stdout.write(f"\r\033[2K")
                        sys.stdout.flush()
                        
                        elapsed_so_far = int(time.time() - start_time)
                        voice_time = max(10, min(30, timeout - elapsed_so_far))
                        voice_ans = self._record_and_transcribe(voice_time)
                        
                        if voice_ans:
                            if user_input and not user_input[-1].isspace():
                                user_input.append(' ')
                            user_input.extend(list(voice_ans))
                            print("\n✏️   Review and edit your answer below (Press Enter to submit):\n", flush=True)
                        else:
                            print("\n⌨️   Type your answer below (Press Enter to submit):\n", flush=True)
                            
                        # Guarantee at least 20 seconds remaining to review/edit
                        spent = int(time.time() - start_time)
                        if timeout - spent < 20:
                            start_time = time.time() - (timeout - 20)

                        # Re-enable cbreak mode
                        tty.setcbreak(fd)
                        break  # Break inner read loop, redraw prompt with new user_input

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
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            except Exception:
                pass
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

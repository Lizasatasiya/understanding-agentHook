import os
import sys
import time
import glob

try:
    import select
    import tty
    import termios
    UNIX_TTY = True
except ImportError:
    UNIX_TTY = False

def _detect_voice():
    if sys.platform == "darwin":
        # Native macOS Dictation requires zero external Python libraries
        return True, "mac_dictation"
    try:
        import numpy
        import whisper
        import sounddevice
        return True, "sounddevice"
    except Exception:
        pass
    return False, None

VOICE_AVAILABLE, AUDIO_BACKEND = _detect_voice()


def _trigger_mac_dictation():
    """Trigger native macOS Dictation via AppleScript."""
    if sys.platform != "darwin":
        return False
    import subprocess
    script = (
        'tell application "System Events"\n'
        '    try\n'
        '        set frontApp to first application process whose frontmost is true\n'
        '        tell frontApp\n'
        '            click (first menu item of menu "Edit" of menu bar 1 whose name starts with "Start Dictation")\n'
        '        end tell\n'
        '    end try\n'
        'end tell'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=1)
        return True
    except Exception:
        return False


def _stop_mac_dictation():
    """Stop native macOS Dictation if active and release the microphone."""
    if sys.platform != "darwin":
        return
    import subprocess
    script = (
        'tell application "System Events"\n'
        '    try\n'
        '        set frontApp to first application process whose frontmost is true\n'
        '        tell frontApp\n'
        '            click (first menu item of menu "Edit" of menu bar 1 whose name starts with "Stop Dictation")\n'
        '        end tell\n'
        '    end try\n'
        'end tell'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=0.5)
    except Exception:
        pass
    try:
        subprocess.run(["killall", "-9", "DictationIM"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


class Interaction:
    def __init__(self):
        self._whisper_model = None

    def timed_input(self, prompt: str, timeout: int, speak_text: str = "", seed_text: str = "") -> str:
        """Read a line from stdin with a live countdown timer.
        
        speak_text: the clean question text to speak aloud (no ANSI codes).
        1. Speaks the question aloud via macOS `say`.
        2. Displays the typing space immediately with [Tab] Mic option if voice is available.
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
            if AUDIO_BACKEND == "mac_dictation":
                print(f"🎙️  {CYAN}{BOLD}[🎙 / Tab]{RESET} {DIM}Dictate answer  │  ⌨️  Type directly{RESET}")
                print(f"💡  {DIM}Press {RESET}{BOLD}[Enter]{RESET}{DIM} to stop mic & review answer  │  Press {RESET}{BOLD}[Enter]{RESET}{DIM} again to submit{RESET}\n", flush=True)
            else:
                print(f"🎙️  {CYAN}{BOLD}[Tab]{RESET} {DIM}Speak with Mic  │  ⌨️  Type directly{RESET}")
                print(f"💡  {DIM}Press {RESET}{BOLD}[Enter]{RESET}{DIM} to stop mic & review answer  │  Press {RESET}{BOLD}[Enter]{RESET}{DIM} again to submit{RESET}\n", flush=True)

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
        if not VOICE_AVAILABLE or not AUDIO_BACKEND:
            return None

        try:
            import numpy as np
            import whisper

            CHUNK_SECS = 3
            RATE = 16000
            CHUNK = 1024
            record_secs = min(timeout, 30)

            print(f"\n🎙️  Listening... (Press Enter when done)\n", flush=True)
            sys.stdout.write(" 🗣️  ")
            sys.stdout.flush()

            if self._whisper_model is None:
                self._whisper_model = whisper.load_model("base")
            model = self._whisper_model

            transcript_parts = []
            elapsed = 0.0
            frames_per_chunk = int(RATE / CHUNK * CHUNK_SECS)
            enter_pressed = False

            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)

            try:
                tty.setcbreak(fd)
                if AUDIO_BACKEND == "sounddevice":
                    import sounddevice as sd
                    with sd.InputStream(samplerate=RATE, channels=1, dtype='float32', blocksize=CHUNK) as stream:
                        while elapsed < record_secs and not enter_pressed:
                            remaining = record_secs - elapsed
                            frames_this_chunk = min(frames_per_chunk, int(RATE / CHUNK * remaining))
                            if frames_this_chunk <= 0:
                                break

                            chunk_frames = []
                            for _ in range(frames_this_chunk):
                                r, _, _ = select.select([sys.stdin], [], [], 0)
                                if r:
                                    key = sys.stdin.read(1)
                                    if key in ('\r', '\n', '\x04'):
                                        enter_pressed = True
                                        break
                                data, _ = stream.read(CHUNK)
                                chunk_frames.append(data)

                            if not chunk_frames:
                                break

                            elapsed += len(chunk_frames) * CHUNK / RATE

                            audio_chunk = np.concatenate(chunk_frames, axis=0).squeeze()
                            if len(audio_chunk) < 1600:
                                audio_chunk = np.pad(audio_chunk, (0, 1600 - len(audio_chunk)))
                            result = model.transcribe(audio_chunk, fp16=False)
                            text = (result.get("text") or "").strip()
                            if text:
                                sys.stdout.write(text + " ")
                                sys.stdout.flush()
                                transcript_parts.append(text)

                            if enter_pressed:
                                break

                elif AUDIO_BACKEND == "pyaudio":
                    import pyaudio
                    FORMAT = pyaudio.paInt16
                    CHANNELS = 1
                    audio_pa = pyaudio.PyAudio()
                    try:
                        stream = audio_pa.open(format=FORMAT, channels=CHANNELS,
                                               rate=RATE, input=True,
                                               frames_per_buffer=CHUNK)
                    except Exception:
                        audio_pa.terminate()
                        return None

                    try:
                        while elapsed < record_secs and not enter_pressed:
                            remaining = record_secs - elapsed
                            frames_this_chunk = min(frames_per_chunk, int(RATE / CHUNK * remaining))
                            if frames_this_chunk <= 0:
                                break

                            chunk_frames = []
                            for _ in range(frames_this_chunk):
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
                            stream.stop_stream()
                            stream.close()
                        except Exception:
                            pass
                        try:
                            audio_pa.terminate()
                        except Exception:
                            pass
            finally:
                try:
                    termios.tcflush(fd, termios.TCIFLUSH)
                except Exception:
                    pass
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

            print("\n", flush=True)
            full_answer = " ".join(transcript_parts).strip()
            if full_answer:
                return full_answer
            return None

        except Exception:
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
        review_mode = False
        
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
                            elif user_input and not review_mode:
                                # Finished speaking/typing: Turn mic OFF and enter editable review mode
                                review_mode = True
                                if AUDIO_BACKEND == "mac_dictation":
                                    _stop_mac_dictation()
                                sys.stdout.write(f"\r\033[2K")
                                print(f"🎙️  \033[93mMic OFF.\033[0m\n✏️   \033[1mReview and edit your answer below\033[0m (Press \033[1m[Enter]\033[0m to submit):\n", flush=True)
                                
                                # Guarantee at least 30s remaining to review/edit
                                spent = int(time.time() - start_time)
                                if timeout - spent < 30:
                                    start_time = time.time() - (timeout - 30)
                                break
                            elif not user_input:
                                # Empty enter, redraw prompt
                                break
                            else:
                                # Already reviewed: Final submit!
                                submitted = True
                                break

                    if ch == '\t' and allow_mic:
                        if AUDIO_BACKEND == "mac_dictation":
                            _trigger_mac_dictation()
                            review_mode = False
                            sys.stdout.write(f"\r\033[2K")
                            print(f"🎙️  \033[92mMac Dictation started!\033[0m Speak your answer now. Press \033[1m[Enter]\033[0m when done speaking.\n", flush=True)
                            spent = int(time.time() - start_time)
                            if timeout - spent < 30:
                                start_time = time.time() - (timeout - 30)
                            break
                        else:
                            # Restore terminal temporarily for voice flow (non-mac)
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
                    if AUDIO_BACKEND == "mac_dictation":
                        _stop_mac_dictation()
                    final_ans = "".join(user_input).strip()
                    # Clear the timer line and print the FULL input so it remains on screen
                    sys.stdout.write(f"\r\033[2K{prompt}{final_ans}\n")
                    sys.stdout.flush()
                    return final_ans
        finally:
            if AUDIO_BACKEND == "mac_dictation":
                _stop_mac_dictation()
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            except Exception:
                pass
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def print_context(self, context: dict):
        pass

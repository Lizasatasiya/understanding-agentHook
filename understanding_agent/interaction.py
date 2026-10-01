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
    def _check():
        try:
            import numpy
            import whisper
            try:
                import sounddevice
                return True, "sounddevice"
            except Exception:
                pass
            try:
                import pyaudio
                return True, "pyaudio"
            except Exception:
                pass
        except Exception:
            pass
        return False, None

    res, backend = _check()
    if res:
        return res, backend

    # If running inside an isolated virtualenv (e.g. pre-commit py_env),
    # dynamically locate and attach host/system site-packages where whisper and audio backends live
    candidate_paths = [
        "/Library/Frameworks/Python.framework/Versions/3.14/lib/python3.14/site-packages",
        "/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/site-packages",
        "/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/site-packages",
        os.path.expanduser("~/Library/Python/3.14/lib/python/site-packages"),
        os.path.expanduser("~/Library/Python/3.12/lib/python/site-packages"),
        "/opt/homebrew/lib/python3.14/site-packages",
        "/opt/homebrew/lib/python3.12/site-packages",
    ]
    candidate_paths.extend(glob.glob("/Library/Frameworks/Python.framework/Versions/*/lib/python*/site-packages"))

    for p in candidate_paths:
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)

    return _check()

VOICE_AVAILABLE, AUDIO_BACKEND = _detect_voice()


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

        # Step 2: Show mic option hint if voice is available or on Mac
        is_mac = sys.platform == 'darwin'
        allow_mic = bool((is_mac or VOICE_AVAILABLE) and speak_text)
        if allow_mic:
            CYAN = "\033[96m"
            BOLD = "\033[1m"
            DIM = "\033[2m"
            RESET = "\033[0m"
            mic_desc = "Speak with Mac Mic" if is_mac else "Speak with Mic"
            print(f"🎙️  {CYAN}{BOLD}[Tab]{RESET} {DIM}{mic_desc}  │  ⌨️  Type answer directly (Enter to submit){RESET}\n", flush=True)

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

    def _record_and_transcribe(self, timeout: int, is_mac: bool = False) -> str | None:
        """Record directly from the microphone and transcribe with Whisper.
        
        - Opens microphone immediately using sounddevice/pyaudio.
        - Displays live audio level meter and countdown timer.
        - Streams audio continuously into a buffer via non-blocking callback.
        - User speaks, and presses Enter when done.
        - Transcribes recorded speech cleanly with Whisper.
        - Returns the transcribed text string.
        """
        if not VOICE_AVAILABLE or not AUDIO_BACKEND:
            return None

        try:
            import numpy as np
            import whisper
            import queue

            RATE = 16000
            CHUNK = 1024
            record_secs = max(10, min(timeout, 45))

            mic_name = "Mac Inbuilt Mic" if is_mac else "Microphone"
            CYAN = "\033[96m"
            GREEN = "\033[92m"
            YELLOW = "\033[93m"
            DIM = "\033[2m"
            BOLD = "\033[1m"
            RESET = "\033[0m"

            print(f"\n{CYAN}🎙️  {mic_name} active (Recording)...{RESET}")
            print(f"   {DIM}Speak your answer. Press {RESET}{BOLD}[Enter]{RESET}{DIM} when finished.{RESET}\n", flush=True)

            if self._whisper_model is None:
                self._whisper_model = whisper.load_model("base")
            model = self._whisper_model

            audio_queue = queue.Queue()
            all_frames = []

            def _audio_callback(indata, frames, time_info, status):
                audio_queue.put(indata.copy())

            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)

            start_time = time.time()
            meter_chars = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
            last_rms = 0.0

            try:
                if AUDIO_BACKEND == "sounddevice":
                    import sounddevice as sd
                    stream = sd.InputStream(samplerate=RATE, channels=1, dtype='float32', blocksize=CHUNK, callback=_audio_callback)
                    with stream:
                        while True:
                            elapsed = time.time() - start_time
                            remaining = int(record_secs - elapsed)
                            if remaining <= 0:
                                break

                            # Drain queue
                            while not audio_queue.empty():
                                blk = audio_queue.get_nowait()
                                all_frames.append(blk)
                                last_rms = float(np.sqrt(np.mean(blk**2)))

                            # Display live audio meter & countdown
                            bar_idx = min(7, int(last_rms * 40))
                            bar = meter_chars[bar_idx] * 4
                            sys.stdout.write(f"\r\033[2K⏱  {remaining:2d}s | {GREEN}● REC{RESET} [{bar:<4}] Speak now... {DIM}(Press Enter when done){RESET} ")
                            sys.stdout.flush()

                            # Check for Enter or Esc
                            r, _, _ = select.select([sys.stdin], [], [], 0.08)
                            if r:
                                ch = sys.stdin.read(1)
                                if ch in ('\r', '\n', '\x04'):
                                    break
                                elif ch == '\x03':
                                    raise KeyboardInterrupt()

                elif AUDIO_BACKEND == "pyaudio":
                    import pyaudio
                    audio_pa = pyaudio.PyAudio()
                    stream = audio_pa.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True, frames_per_buffer=CHUNK)
                    try:
                        while True:
                            elapsed = time.time() - start_time
                            remaining = int(record_secs - elapsed)
                            if remaining <= 0:
                                break

                            data = stream.read(CHUNK, exception_on_overflow=False)
                            blk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                            all_frames.append(blk)
                            last_rms = float(np.sqrt(np.mean(blk**2)))

                            bar_idx = min(7, int(last_rms * 40))
                            bar = meter_chars[bar_idx] * 4
                            sys.stdout.write(f"\r\033[2K⏱  {remaining:2d}s | {GREEN}● REC{RESET} [{bar:<4}] Speak now... {DIM}(Press Enter when done){RESET} ")
                            sys.stdout.flush()

                            r, _, _ = select.select([sys.stdin], [], [], 0.08)
                            if r:
                                ch = sys.stdin.read(1)
                                if ch in ('\r', '\n', '\x04'):
                                    break
                                elif ch == '\x03':
                                    raise KeyboardInterrupt()
                    finally:
                        stream.stop_stream()
                        stream.close()
                        audio_pa.terminate()

            finally:
                try:
                    termios.tcflush(fd, termios.TCIFLUSH)
                except Exception:
                    pass
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

            sys.stdout.write(f"\r\033[2K⚡ Transcribing speech...\n")
            sys.stdout.flush()

            while not audio_queue.empty():
                all_frames.append(audio_queue.get_nowait())

            if not all_frames:
                return None

            audio_data = np.concatenate(all_frames, axis=0).flatten()
            if len(audio_data) < 8000:
                return None

            result = model.transcribe(audio_data, fp16=False)
            text = (result.get("text") or "").strip()
            if text:
                print(f"✓ Transcribed: \"{text}\"\n", flush=True)
                return text
            return None

        except Exception as e:
            print(f"\n[Microphone error: {e}]", flush=True)
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
                        is_mac = sys.platform == 'darwin'
                        
                        # Temporarily restore terminal for recording flow
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                        sys.stdout.write(f"\r\033[2K")
                        sys.stdout.flush()
                        
                        elapsed_so_far = int(time.time() - start_time)
                        voice_time = max(10, min(45, timeout - elapsed_so_far))
                        voice_ans = self._record_and_transcribe(voice_time, is_mac=is_mac)
                        
                        if voice_ans:
                            if user_input and not user_input[-1].isspace():
                                user_input.append(' ')
                            user_input.extend(list(voice_ans))
                            print("✏️   Review and edit your answer below (Press Enter to submit):\n", flush=True)
                        else:
                            print("⌨️   Type your answer below (Press Enter to submit):\n", flush=True)
                            
                        # Guarantee at least 25 seconds remaining to review/edit
                        spent = int(time.time() - start_time)
                        if timeout - spent < 25:
                            start_time = time.time() - (timeout - 25)

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
        CYAN = "\033[96m"
        BOLD = "\033[1m"
        DIM = "\033[2m"
        RESET = "\033[0m"
        YELLOW = "\033[93m"
        GREEN = "\033[92m"
        RED = "\033[91m"

        print(f"\n{BOLD}          CODE UNDERSTANDING CHECK{RESET}\n")

        if context.get("is_large_change", False):
            stats = context.get("stats", {})
            added = stats.get("total_added", 0)
            deleted = stats.get("total_deleted", 0)
            files = context.get("file_summary", [])
            summary = context.get("summary", {})

            
            print(f"{BOLD}{CYAN} ARCHITECTURAL CHANGE OVERVIEW ({GREEN}+{added}{CYAN}/{RED}-{deleted}{CYAN} lines, {len(files)} files){' ' * max(0, 24 - len(str(added)) - len(str(deleted)) - len(str(len(files))))}{RESET}")
            
            print(f"\n{BOLD}Touched Components:{RESET}")
            for item in files:
                funcs = ", ".join(item.get("functions", [])) or "module-level changes"
                print(f"  • {CYAN}{item['file']}{RESET}: {funcs}")

            print(f"\n{BOLD}[ARCHITECTURAL SUMMARY]{RESET}")
            print(f"{BOLD}What Changed:{RESET}   {summary.get('what_changed', 'N/A')}")
            print(f"{BOLD}Impact:{RESET}         {summary.get('impact', 'N/A')}")
            print(f"{BOLD}Why It Matters:{RESET} {summary.get('why_it_matters', 'N/A')}")
            if summary.get("key_risks"):
                print(f"{BOLD}Key Invariants:{RESET} {summary.get('key_risks')}")
            print("\n")
            return

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

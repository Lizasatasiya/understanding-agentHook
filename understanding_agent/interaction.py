import os
import sys
import time
import glob
import re
import shutil
import unicodedata

try:
    import select
    import tty
    import termios
    UNIX_TTY = True
except ImportError:
    UNIX_TTY = False


def _str_display_width(s: str) -> int:
    """Calculate visible width of a string in terminal columns, ignoring ANSI escape sequences."""
    clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', s)
    w = 0
    for ch in clean:
        if unicodedata.combining(ch):
            continue
        east = unicodedata.east_asian_width(ch)
        if east in ('F', 'W'):
            w += 2
        else:
            w += 1
    return w


def _get_terminal_cols(fd: int = None) -> int:
    """Get the true terminal column width using all available descriptors."""
    if fd is not None:
        try:
            return os.get_terminal_size(fd).columns
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            if stream and hasattr(stream, 'fileno'):
                return os.get_terminal_size(stream.fileno()).columns
        except Exception:
            pass
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        pass
    try:
        return os.get_terminal_size().columns
    except Exception:
        pass
    return 80

def _detect_voice():
    if sys.platform == "darwin":
        # Native macOS Dictation requires zero external Python libraries
        return True, "mac_dictation"
    # If running inside an isolated pre-commit venv, search base/system python site-packages
    try:
        base_site = os.path.join(
            sys.base_prefix, 'lib', f'python{sys.version_info.major}.{sys.version_info.minor}', 'site-packages'
        )
        if base_site not in sys.path and os.path.exists(base_site):
            sys.path.append(base_site)
    except Exception:
        pass

    try:
        venv = os.environ.get("VIRTUAL_ENV")
        if venv:
            for p in glob.glob(os.path.join(venv, "lib", "python*", "site-packages")):
                if p not in sys.path:
                    sys.path.insert(0, p)
    except Exception:
        pass

    try:
        import numpy
        import whisper
        import sounddevice
        return True, "sounddevice"
    except Exception:
        pass

    if sys.platform == "darwin":
        # Native macOS Dictation fallback
        return True, "mac_dictation"
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
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=0.8)
    except Exception:
        pass
    try:
        subprocess.run(["killall", "-9", "DictationIM"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _reconcile_transcription(existing: str, incoming: str) -> str:
    """Smartly merge streamed transcription with late-arriving dictation flush.
    
    Prevents duplicates (e.g. 'Hello hello' + 'Hello hello') while capturing
    any missing trailing words (e.g. 'Hello' + 'hello I don't know') and
    cleaning up any trailing stray characters.
    """
    existing = existing.strip()
    incoming = incoming.strip()
    if not existing:
        return incoming
    if not incoming:
        return existing
    if existing == incoming:
        return existing

    # Detect double repetition flush from OS input method
    if incoming == existing + existing or incoming == existing + " " + existing:
        return existing
    if existing == incoming + incoming or existing == incoming + " " + incoming:
        return incoming

    # Stray trailing single letter (e.g. "don't knowH" vs "don't know")
    if len(existing) == len(incoming) + 1 and existing.startswith(incoming):
        return incoming
    if len(incoming) == len(existing) + 1 and incoming.startswith(existing):
        return existing

    # Incoming is an extended version of existing
    if incoming.startswith(existing):
        return incoming
    # Existing strictly longer and extends incoming
    if existing.startswith(incoming):
        return existing
    if existing.rstrip().endswith(incoming):
        return existing
    if incoming.rstrip().endswith(existing):
        return incoming

    # Character-level overlap check
    min_len = min(len(existing), len(incoming))
    for k in range(min_len, 0, -1):
        if existing[-k:] == incoming[:k]:
            return existing + incoming[k:]

    # Word-level overlap check
    ex_words = existing.split()
    in_words = incoming.split()
    max_w = min(len(ex_words), len(in_words))
    for w in range(max_w, 0, -1):
        if ex_words[-w:] == in_words[:w]:
            return " ".join(ex_words + in_words[w:])

    return f"{existing} {incoming}".strip()

def _prev_word(pos: int, chars: list) -> int:
    while pos > 0 and chars[pos - 1].isspace():
        pos -= 1
    while pos > 0 and not chars[pos - 1].isspace():
        pos -= 1
    return pos


def _next_word(pos: int, chars: list) -> int:
    n = len(chars)
    while pos < n and not chars[pos].isspace():
        pos += 1
    while pos < n and chars[pos].isspace():
        pos += 1
    return pos


def _get_display_window(text: str, cursor_pos: int, avail_width: int):
    n = len(text)
    if n <= avail_width:
        return text, cursor_pos
    
    window_content_len = max(10, avail_width - 6)
    half = window_content_len // 2
    
    s = cursor_pos - half
    if s <= 3:
        end_idx = avail_width - 3
        disp_str = text[:end_idx] + "..."
        disp_cursor = cursor_pos
        return disp_str, disp_cursor
    
    e = s + window_content_len
    if e >= n - 3:
        start_idx = n - (avail_width - 3)
        disp_str = "..." + text[start_idx:]
        disp_cursor = 3 + (cursor_pos - start_idx)
        return disp_str, disp_cursor
    
    disp_str = "..." + text[s:e] + "..."
    disp_cursor = 3 + (cursor_pos - s)
    return disp_str, disp_cursor


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
        hint_shown = False
        allow_mic = bool(VOICE_AVAILABLE and speak_text)
        if allow_mic:
            CYAN = "\033[96m"
            BOLD = "\033[1m"
            DIM = "\033[2m"
            RESET = "\033[0m"
            if AUDIO_BACKEND == "mac_dictation":
                print(f"🎙️  {CYAN}{BOLD}[🎙 / Tab]{RESET} {DIM}Dictate answer  │  ⌨️  Type directly{RESET}")
                hint_shown = True
            else:
                print(f"🎙️  {CYAN}{BOLD}[Tab]{RESET} {DIM}Speak with Mic  │  ⌨️  Type directly{RESET}")
                hint_shown = True

        if not UNIX_TTY:
            return self._timed_input_windows(prompt, timeout, seed_text=seed_text, allow_mic=allow_mic)
        else:
            return self._timed_input_unix(prompt, timeout, seed_text=seed_text, allow_mic=allow_mic, hint_shown=hint_shown)

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
            all_frames = []
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

                            all_frames.extend(chunk_frames)
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
            if all_frames:
                full_audio = np.concatenate(all_frames, axis=0).squeeze()
                if len(full_audio) >= 1600:
                    try:
                        full_res = model.transcribe(full_audio, fp16=False)
                        full_trans = (full_res.get("text") or "").strip()
                        if full_trans:
                            return full_trans
                    except Exception:
                        pass
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
                
            timer_str = f"⏱ {remaining:2d}s"
            cols = _get_terminal_cols()
            clean_prompt = prompt.lstrip()
            prefix_str = f"{timer_str} │ {clean_prompt}"
            prefix_width = _str_display_width(prefix_str)
            avail_width = max(15, cols - prefix_width - 2)
            current_str = "".join(user_input)
            display_str, _ = _get_display_window(current_str, len(user_input), avail_width)
            sys.stdout.write(f"\r{prefix_str}{display_str} ")
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

    def _timed_input_unix(self, prompt: str, timeout: int, seed_text: str = "", allow_mic: bool = False, hint_shown: bool = False) -> str:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        
        start_time = time.time()
        user_input = list(seed_text) if seed_text else []
        cursor_pos = len(user_input)
        review_mode = False
        dictation_active = False
        temp_lines = 0
        
        try:
            tty.setcbreak(fd)
            while True:
                remaining = int(timeout - (time.time() - start_time))
                if remaining <= 0:
                    sys.stdout.write(f"\r\033[2K⏰ Time's up! ({timeout}s limit reached)\n")
                    sys.stdout.flush()
                    return None
                    
                timer_str = f"⏱ {remaining:2d}s"
                cols = _get_terminal_cols(fd)
                clean_prompt = prompt.lstrip()
                prefix_str = f"{timer_str} │ {clean_prompt}"
                prefix_width = _str_display_width(prefix_str)
                # Maximize single-line visible width: reserve only prefix width + 2 col safety margin
                avail_width = max(15, cols - prefix_width - 2)
                current_str = "".join(user_input)
                display_str, disp_cursor = _get_display_window(current_str, cursor_pos, avail_width)

                sys.stdout.write(f"\r\033[2K{prefix_str}{display_str}")
                
                # Reposition cursor if not at the end of visible text
                back_steps = len(display_str) - disp_cursor
                if back_steps > 0:
                    sys.stdout.write(f"\033[{back_steps}D")
                sys.stdout.flush()
                
                # Wait up to 0.2s for any input on raw OS file descriptor
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    continue

                try:
                    raw_bytes = os.read(fd, 1024)
                except Exception:
                    break
                if not raw_bytes:
                    continue

                chunk = raw_bytes.decode('utf-8', 'ignore')
                if not chunk:
                    continue

                # If escape sequence was somehow fragmented across packet boundary, read remaining bytes
                if chunk == '\x1b' or (chunk.startswith('\x1b') and not (chunk[-1].isalpha() or chunk[-1] == '~')):
                    r_tail, _, _ = select.select([fd], [], [], 0.05)
                    if r_tail:
                        try:
                            chunk += os.read(fd, 64).decode('utf-8', 'ignore')
                        except Exception:
                            pass

                if review_mode:
                    # Keep timer healthy while user is actively reviewing/editing
                    spent_now = int(time.time() - start_time)
                    if timeout - spent_now < 30:
                        start_time = time.time() - (timeout - 30)

                # 1. Handle escape sequences (Arrow keys, Option+Arrows, Home, End, Delete)
                if chunk.startswith('\x1b'):
                    if chunk in ('\x1b[D', '\x1bOD'):  # Left arrow
                        cursor_pos = max(0, cursor_pos - 1)
                    elif chunk in ('\x1b[C', '\x1bOC'):  # Right arrow
                        cursor_pos = min(len(user_input), cursor_pos + 1)
                    elif chunk in ('\x1b[A', '\x1bOA', '\x1b[H', '\x1bOH', '\x1b[1~', '\x1b[7~', '\x1b[5~'):  # Up / Home / PgUp
                        cursor_pos = 0
                    elif chunk in ('\x1b[B', '\x1bOB', '\x1b[F', '\x1bOF', '\x1b[4~', '\x1b[8~', '\x1b[6~'):  # Down / End / PgDn
                        cursor_pos = len(user_input)
                    elif chunk in ('\x1bb', '\x1b[1;3D', '\x1b[1;5D', '\x1b[5D', '\x1b[3D'):  # Option+Left (word back)
                        cursor_pos = _prev_word(cursor_pos, user_input)
                    elif chunk in ('\x1bf', '\x1b[1;3C', '\x1b[1;5C', '\x1b[5C', '\x1b[3C'):  # Option+Right (word forward)
                        cursor_pos = _next_word(cursor_pos, user_input)
                    elif chunk in ('\x1b[3~',):  # Delete key
                        if cursor_pos < len(user_input):
                            user_input.pop(cursor_pos)
                    elif chunk in ('\x1b\x7f', '\x1b\x08'):  # Option+Backspace (delete word backward)
                        new_pos = _prev_word(cursor_pos, user_input)
                        del user_input[new_pos:cursor_pos]
                        cursor_pos = new_pos
                    # All other escape codes are completely swallowed!
                    continue

                # 2. Handle Enter
                if '\r' in chunk or '\n' in chunk:
                    curr_text = "".join(user_input).strip().lower()
                    if allow_mic and curr_text in ("/mic", ":mic", "mic"):
                        user_input.clear()
                        cursor_pos = 0
                        chunk = '\t'  # route to mic trigger below
                    elif dictation_active:
                        # User pressed Enter while dictating -> Stop mic & show review/edit prompt
                        if AUDIO_BACKEND == "mac_dictation":
                            _stop_mac_dictation()

                        # Drain in-flight transcription directly from OS fd
                        incoming_chars = []
                        drain_end = time.time() + 0.35
                        while time.time() < drain_end:
                            r_pending, _, _ = select.select([fd], [], [], 0.05)
                            if not r_pending:
                                if incoming_chars:
                                    break
                                continue
                            try:
                                d_bytes = os.read(fd, 1024)
                            except Exception:
                                break
                            if not d_bytes:
                                break
                            d_str = d_bytes.decode('utf-8', 'ignore')
                            if d_str.startswith('\x1b') or d_str in ('\r', '\n'):
                                continue
                            for c in d_str:
                                if c in ('\x08', '\x7f'):
                                    if incoming_chars:
                                        incoming_chars.pop()
                                elif c.isprintable():
                                    incoming_chars.append(c)

                        merged_text = _reconcile_transcription("".join(user_input), "".join(incoming_chars))
                        user_input = list(merged_text)
                        cursor_pos = len(user_input)
                        review_mode = True
                        dictation_active = False

                        sys.stdout.write("\r\033[2K")
                        if hint_shown:
                            sys.stdout.write("\033[1A\033[2K")
                            hint_shown = False
                        for _ in range(temp_lines):
                            sys.stdout.write("\033[1A\033[2K")
                        sys.stdout.write("\r")
                        sys.stdout.flush()

                        print(f"🎙️  \033[93mMic OFF.\033[0m\n✏️   \033[1mReview and edit your answer below\033[0m (Press \033[1m[Enter]\033[0m to submit):\n", flush=True)
                        temp_lines = 3

                        # Guarantee at least 60 seconds in review mode
                        spent = int(time.time() - start_time)
                        if timeout - spent < 60:
                            start_time = time.time() - (timeout - 60)
                        continue
                    elif not user_input:
                        # Empty enter, redraw prompt
                        continue
                    else:
                        # Direct typing or review mode complete -> Final submit!
                        submitted = True
                        break

                # 3. Handle Tab (Mic trigger)
                if chunk == '\t' and allow_mic:
                    if AUDIO_BACKEND == "mac_dictation":
                        _trigger_mac_dictation()
                        dictation_active = True
                        review_mode = False
                        sys.stdout.write("\r\033[2K")
                        if hint_shown:
                            sys.stdout.write("\033[1A\033[2K")
                            hint_shown = False
                        for _ in range(temp_lines):
                            sys.stdout.write("\033[1A\033[2K")
                        sys.stdout.write("\r")
                        sys.stdout.flush()
                        print(f"🎙️  \033[92mDictation started!\033[0m Speak your answer now. Press \033[1m[Enter]\033[0m when done speaking.\n", flush=True)
                        temp_lines = 2
                        spent = int(time.time() - start_time)
                        if timeout - spent < 30:
                            start_time = time.time() - (timeout - 30)
                        continue
                    else:
                        # Non-mac voice flow
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                        sys.stdout.write("\r\033[2K")
                        if hint_shown:
                            sys.stdout.write("\033[1A\033[2K")
                            hint_shown = False
                        for _ in range(temp_lines):
                            sys.stdout.write("\033[1A\033[2K")
                        sys.stdout.write("\r")
                        sys.stdout.flush()
                        
                        elapsed_so_far = int(time.time() - start_time)
                        voice_time = max(10, min(30, timeout - elapsed_so_far))
                        voice_ans = self._record_and_transcribe(voice_time)
                        
                        if voice_ans:
                            if user_input and not user_input[-1].isspace():
                                user_input.append(' ')
                            user_input.extend(list(voice_ans))
                            cursor_pos = len(user_input)
                            review_mode = True
                            print("\n✏️   Review and edit your answer below (Press Enter to submit):\n", flush=True)
                            temp_lines = 3
                        else:
                            print("\n⌨️   Type your answer below (Press Enter to submit):\n", flush=True)
                            temp_lines = 3
                            
                        spent = int(time.time() - start_time)
                        if timeout - spent < 45:
                            start_time = time.time() - (timeout - 45)

                        tty.setcbreak(fd)
                        continue

                # 4. Handle control characters
                if chunk in ('\x08', '\x7f'):  # Backspace
                    if cursor_pos > 0:
                        user_input.pop(cursor_pos - 1)
                        cursor_pos -= 1
                elif chunk == '\x03':  # Ctrl-C
                    raise KeyboardInterrupt()
                elif chunk == '\x04':  # Ctrl-D
                    submitted = True
                    break
                elif chunk == '\x01':  # Ctrl-A (Home)
                    cursor_pos = 0
                elif chunk == '\x05':  # Ctrl-E (End)
                    cursor_pos = len(user_input)
                elif chunk == '\x17':  # Ctrl-W (Word backspace)
                    new_pos = _prev_word(cursor_pos, user_input)
                    del user_input[new_pos:cursor_pos]
                    cursor_pos = new_pos
                elif chunk == '\x15':  # Ctrl-U (Clear to start)
                    del user_input[:cursor_pos]
                    cursor_pos = 0
                elif chunk == '\x0b':  # Ctrl-K (Clear to end)
                    del user_input[cursor_pos:]
                else:
                    # 5. Regular typing, paste, or streamed transcription
                    chars = [c for c in chunk if c.isprintable() or c == ' ']
                    if chars:
                        user_input[cursor_pos:cursor_pos] = chars
                        cursor_pos += len(chars)

            if submitted:
                if AUDIO_BACKEND == "mac_dictation":
                    _stop_mac_dictation()
                final_ans = "".join(user_input).strip()
                sys.stdout.write("\r\033[2K")
                if hint_shown:
                    sys.stdout.write("\033[1A\033[2K")
                    hint_shown = False
                for _ in range(temp_lines):
                    sys.stdout.write("\033[1A\033[2K")
                sys.stdout.write(f"\r{prompt}{final_ans}\n")
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

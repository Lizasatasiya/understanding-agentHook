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
    def timed_input(self, prompt: str, timeout: int) -> str:
        """Read a line from stdin with a live countdown timer.
        
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
        if UNIX_TTY and VOICE_AVAILABLE:
            voice_ans = self._try_voice_answer(prompt, timeout)
            if voice_ans is not None:
                return voice_ans
            # Voice unavailable or failed — fall through to typed input

        if not UNIX_TTY:
            return self._timed_input_windows(prompt, timeout)
        else:
            return self._timed_input_unix(prompt, timeout)

    def _try_voice_answer(self, prompt: str, timeout: int) -> str | None:
        """Speak the question and capture a spoken answer via Whisper.
        
        Returns the transcribed text, or None if voice is not available
        so the caller can fall back to typed input.
        """
        try:
            # Speak the question aloud so the developer hears it clearly
            print(f"\n🎙️  Listening... (speak your answer, up to {timeout}s)\n")
            _mic.speak_blocking(prompt)
            # Record for up to `timeout` seconds
            answer = _mic.voice_answer(seconds=min(timeout, 30))
            if answer:
                print(f"\n 🗣️  You said: {answer}\n")
                return answer
            return None
        except _mic.MicUnavailableError:
            print("  [🎙️  Mic unavailable — switching to typed input]")
            return None
        except Exception:
            # Whisper not installed, ffmpeg missing, etc. — degrade silently
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

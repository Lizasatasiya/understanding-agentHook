import os
import sys
import subprocess
import ctypes
import shlex
from typing import Optional, List, Tuple


def get_head_commit(repo_root: Optional[str] = None) -> str:
    """Return the current HEAD commit hash, or 'INITIAL_COMMIT' if no commits exist."""
    cwd = repo_root or os.getcwd()
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()
        if out:
            return out
    except Exception:
        pass
    return "INITIAL_COMMIT"


def get_process_args_darwin(pid: int) -> Optional[List[str]]:
    """Retrieve exact process arguments on macOS using KERN_PROCARGS2 via ctypes."""
    try:
        CTL_KERN = 1
        KERN_PROCARGS2 = 49
        mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
        size = ctypes.c_size_t(0)
        libc = ctypes.CDLL(None)
        res = libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0)
        if res != 0 or size.value == 0:
            return None
        buf = ctypes.create_string_buffer(size.value)
        res = libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0)
        if res != 0:
            return None
        raw = buf.raw
        argc = int.from_bytes(raw[:4], byteorder="little")
        pos = 4
        while pos < len(raw) and raw[pos] != 0:
            pos += 1
        while pos < len(raw) and raw[pos] == 0:
            pos += 1
        args = []
        for _ in range(argc):
            start = pos
            while pos < len(raw) and raw[pos] != 0:
                pos += 1
            args.append(raw[start:pos].decode("utf-8", errors="replace"))
            pos += 1
        return args
    except Exception:
        return None


def get_process_args_linux(pid: int) -> Optional[List[str]]:
    """Retrieve process arguments on Linux via /proc/{pid}/cmdline."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            data = f.read()
        return [arg.decode("utf-8", errors="replace") for arg in data.split(b"\0") if arg]
    except Exception:
        return None


def get_process_args(pid: int) -> Optional[List[str]]:
    """Get process command-line arguments across platforms."""
    if sys.platform == "darwin":
        args = get_process_args_darwin(pid)
        if args is not None:
            return args
    elif sys.platform.startswith("linux"):
        args = get_process_args_linux(pid)
        if args is not None:
            return args
    try:
        out = subprocess.check_output(
            ["ps", "-o", "args=", "-p", str(pid)],
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()
        if out:
            return shlex.split(out)
    except Exception:
        pass
    return None


def get_parent_pid(pid: int) -> Optional[int]:
    """Get the parent PID of a given process."""
    try:
        out = subprocess.check_output(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()
        if out:
            return int(out.split()[0])
    except Exception:
        pass
    return None


def extract_commit_message_from_args(args: List[str]) -> Optional[str]:
    """Extract the commit message from a git commit argument list."""
    if not args:
        return None

    # Check if this command is git commit
    is_git_commit = False
    for i in range(len(args)):
        arg = args[i].lower()
        if (arg == "git" or arg.endswith("/git")) and i + 1 < len(args) and args[i + 1].lower() == "commit":
            is_git_commit = True
            break
    if not is_git_commit:
        return None

    messages = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-m", "--message"):
            if i + 1 < len(args):
                messages.append(args[i + 1])
                i += 2
                continue
        elif a.startswith("--message="):
            messages.append(a.split("=", 1)[1])
        elif a in ("-F", "--file"):
            if i + 1 < len(args) and os.path.isfile(args[i + 1]):
                try:
                    with open(args[i + 1], "r", encoding="utf-8") as f:
                        messages.append(f.read().strip())
                except Exception:
                    pass
                i += 2
                continue
        elif a.startswith("--file="):
            fpath = a.split("=", 1)[1]
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        messages.append(f.read().strip())
                except Exception:
                    pass
        i += 1

    if messages:
        return "\n\n".join(messages)
    return ""


def find_git_commit_ancestor() -> Tuple[Optional[int], Optional[List[str]]]:
    """Traverse up process ancestry to find the initiating git commit process."""
    pid = os.getpid()
    for _ in range(30):
        ppid = get_parent_pid(pid)
        if not ppid or ppid <= 1 or ppid == pid:
            break
        args = get_process_args(ppid)
        if args:
            for i in range(len(args)):
                arg = args[i].lower()
                if (arg == "git" or arg.endswith("/git")) and i + 1 < len(args) and args[i + 1].lower() == "commit":
                    return ppid, args
        pid = ppid
    return None, None


def get_current_commit_message(repo_root: Optional[str] = None) -> str:
    """
    Resolve the commit message for the active commit attempt.
    1. Check ancestor git commit command line (-m / --message / -F)
    2. Check environment variables (COMMIT_MESSAGE, GIT_COMMIT_MSG)
    3. Return empty string if interactive/unspecified.
    """
    # 1. Inspect ancestor process tree
    _, args = find_git_commit_ancestor()
    if args:
        msg = extract_commit_message_from_args(args)
        if msg is not None:
            return msg.strip()

    # 2. Inspect environment variables
    env_msg = os.environ.get("COMMIT_MESSAGE") or os.environ.get("GIT_COMMIT_MSG")
    if env_msg:
        return env_msg.strip()

    return ""

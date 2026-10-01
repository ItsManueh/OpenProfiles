"""
Development mode: opens the interface and restarts it whenever a .py file is saved.

    python dev.py        (or: start.bat dev)

Before restarting, the code is checked to compile; on a syntax error the error
is shown here and the running app keeps working. The restart closes the app
gracefully, so open profiles save their session (their windows close and have
to be reopened).
"""

from __future__ import annotations

import py_compile
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POLL_SECONDS = 0.5
STOP_TIMEOUT = 30  # seconds to close the app before forcing it


def log(message: str) -> None:
    print(f"[dev {time.strftime('%H:%M:%S')}] {message}", flush=True)


def snapshot() -> dict[Path, float]:
    """Modification time of every .py file of the app (project root and the ui package)."""
    times: dict[Path, float] = {}
    for path in [*BASE_DIR.glob("*.py"), *(BASE_DIR / "ui").rglob("*.py")]:
        try:
            times[path] = path.stat().st_mtime
        except OSError:  # deleted or renamed while scanning (editors save through temporary files)
            continue
    return times


def compile_errors(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(e.msg.strip())
        except OSError:  # deleted since it was seen; nothing to check
            continue
    return errors


# ----------------------------------------------------------------------------
# Child process: the interface, which closes gracefully when it receives a
# line on its standard input.
# ----------------------------------------------------------------------------


def run_app() -> None:
    from PySide6.QtCore import QTimer

    from ui.app import create_app

    app, window = create_app()
    stop_requested = threading.Event()

    def wait_for_stop() -> None:
        sys.stdin.readline()  # also returns if the watcher dies (end of input)
        stop_requested.set()

    def poll_stop() -> None:
        if stop_requested.is_set():
            timer.stop()
            window.close()  # closes the profiles, saving their sessions

    threading.Thread(target=wait_for_stop, daemon=True).start()
    timer = QTimer()
    timer.timeout.connect(poll_stop)
    timer.start(200)
    app.exec()


# ----------------------------------------------------------------------------
# Watcher process
# ----------------------------------------------------------------------------


def start_app() -> subprocess.Popen[str]:
    log("Starting the app…")
    # Own process group: Ctrl+C only reaches the watcher, which closes the app gracefully.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--app"],
        stdin=subprocess.PIPE,
        text=True,
        cwd=BASE_DIR,
        creationflags=flags,
    )


def stop_app(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    log("Closing the app (saving sessions)…")
    try:
        if process.stdin is not None:
            process.stdin.write("stop\n")
            process.stdin.flush()
        process.wait(timeout=STOP_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        log("The app did not respond; forcing it to close.")
        process.kill()
        process.wait()


def watch() -> None:
    log(f"Development mode in {BASE_DIR}")
    log("Save any .py file to restart the app. Ctrl+C to quit.")
    files = snapshot()
    process = start_app()
    exit_reported = False
    try:
        while True:
            time.sleep(POLL_SECONDS)
            if process.poll() is not None and not exit_reported:
                exit_reported = True
                log(f"The app closed (exit code {process.returncode}). Save a change to reopen it.")

            current = snapshot()
            if current == files:
                continue
            time.sleep(0.3)  # editors sometimes save in several steps
            current = snapshot()
            changed = [path for path, mtime in current.items() if files.get(path) != mtime]
            files = current
            if not changed:
                continue
            log("Changed: " + ", ".join(path.name for path in changed))

            errors = compile_errors(changed)
            if errors:
                log("Syntax error; the app keeps running the previous version:")
                for error in errors:
                    print(error, flush=True)
                continue
            stop_app(process)
            process = start_app()
            exit_reported = False
    except KeyboardInterrupt:
        log("Leaving development mode…")
        stop_app(process)


if __name__ == "__main__":
    if "--app" in sys.argv:
        run_app()
    else:
        watch()

"""Start the Windows editor without retaining the launcher's console window."""

from pathlib import Path
import subprocess
import sys
from .runtime import is_frozen


def launch_gui(root: Path | None = None) -> int:
    """Return the new editor PID, using the already validated Python interpreter.

    The child receives its own copy of the log handle. Closing this short-lived
    bootstrap and its batch console therefore leaves both the GUI and logging
    running, without relying on a separate pythonw installation or shell quoting.
    The batch file must finish its log redirections before calling this module:
    an active cmd.exe write handle can prevent this open on Windows.
    """
    if sys.platform != "win32":
        raise RuntimeError("The Dolly desktop launcher requires Windows.")
    if is_frozen():
        raise RuntimeError("Launch Dolly.exe directly; the Python bootstrap is only for source installations.")
    root = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[1]
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "Dolly_startup.log").open("ab", buffering=0) as startup_log:
        child = subprocess.Popen(
            [sys.executable, "-u", "-m", "dolly"],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=startup_log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )
        try:
            startup_log.write(f"Dolly editor started (PID {child.pid}).\r\n".encode("utf-8"))
        except OSError:
            pass  # The editor already owns its inherited log handle.
    return child.pid


def main() -> int:
    try:
        launch_gui()
    except (OSError, RuntimeError) as error:
        message = f"Could not start the Dolly editor: {error}"
        print(message, file=sys.stderr)
        # The BAT no longer redirects this process into the file we open.
        # Record a failed launch after launch_gui has released its handle;
        # keep the console error visible if the file is actually unwritable.
        startup_log = Path(__file__).resolve().parents[1] / "logs" / "Dolly_startup.log"
        try:
            with startup_log.open("a", encoding="utf-8") as output:
                output.write(message + "\n")
        except OSError:
            pass
        print("See logs/Dolly_startup.log in the Dolly folder.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

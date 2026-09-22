"""Bound owned process groups and retain terminal evidence before returning."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


def seconds(name, default):
    value = float(os.environ.get(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be finite and positive")
    return value


def bounded_process(args, *, cwd, env, log_path, timeout, receipt_path,
                    metadata, grace=3):
    """A fresh session owns all descendants; TERM grace then KILL, always wait.

    Descendants inherit this process group, including shell/pytest children.
    The group is killed even when its leader has already exited. Reaping of
    orphan grandchildren belongs to the OS; the directly owned child is waited.
    """
    process = None
    previous = {}
    result = {**metadata, "status": "failed", "exit_code": None, "reason": "interrupted"}
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, interrupt)
        atomic_json(receipt_path, {**result, "status": "pending", "reason": "running"})
        with Path(log_path).open("w") as log:
            process = subprocess.Popen(args, cwd=cwd, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            result["pid"] = process.pid
            atomic_json(receipt_path, {**result, "status": "pending", "reason": "running"})
            code = process.wait(timeout=timeout)
            result.update(exit_code=code, status="passed" if code == 0 else "failed", reason="exit")
    except subprocess.TimeoutExpired:
        result.update(exit_code=124, reason="timeout")
    except KeyboardInterrupt:
        result.update(exit_code=130, reason="interrupted")
    except OSError:
        result.update(exit_code=127, reason="spawn failed")
    finally:
        # Repeated interruption cannot abandon the owned tree during cleanup.
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        try:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    deadline = time.monotonic() + grace
                    while time.monotonic() < deadline:
                        try:
                            os.killpg(process.pid, 0)
                        except ProcessLookupError:
                            break
                        time.sleep(min(.05, max(0, deadline - time.monotonic())))
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    result.update(status="failed", termination_error=type(exc).__name__)
                    # Still reap/stop our directly owned child where permitted.
                    # A denied group kill can never be represented as success.
                    try:
                        process.kill()
                    except OSError:
                        pass
                try:
                    process.wait(timeout=grace)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    result.update(status="failed", termination_error=type(exc).__name__)
        finally:
            try:
                atomic_json(receipt_path, result)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
    return result

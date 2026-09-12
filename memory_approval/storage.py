"""Atomic JSON transactions shared by worker and approval server (macOS/Linux)."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import json
import os
import tempfile
import threading

_thread_lock = threading.RLock()


def runtime_path(name):
    root = os.environ.get("DEV_C_DATA_DIR")
    return (Path(root) if root else Path(__file__).resolve().parents[1] / "runtime") / name


@contextmanager
def transaction(path, default_factory):
    """Lock across processes; corruption raises instead of overwriting existing data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock, open(str(path) + ".lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if path.exists():
                with path.open(encoding="utf-8") as source:
                    data = json.load(source)
            else:
                data = default_factory()
            before = json.dumps(data, sort_keys=True, allow_nan=False)
            yield data
            after = json.dumps(data, sort_keys=True, indent=2, allow_nan=False)
            if not path.exists() or before != json.dumps(data, sort_keys=True, allow_nan=False):
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                                     prefix=".json-", delete=False) as output:
                        temporary = output.name
                        output.write(after + "\n")
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, path)
                finally:
                    if temporary and os.path.exists(temporary):
                        os.unlink(temporary)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

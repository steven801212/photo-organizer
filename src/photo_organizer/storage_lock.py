"""In-process leases for the single Organizer Service serving each library.

Normal SQLite connections may coexist. Restore waits for all of them to close;
filesystem mutations are serialized per library, including scheduled maintenance.
"""
from contextlib import contextmanager
from pathlib import Path
from threading import Condition, Lock, RLock, get_ident


class StorageLock:
    def __init__(self):
        self.operation = RLock()
        self.schema = RLock()
        self.condition = Condition(RLock())
        self.readers = {}
        self.writer = None
        self.waiting_writers = 0

    @contextmanager
    def read(self):
        owner = get_ident()
        with self.condition:
            while ((self.writer is not None and self.writer != owner) or
                   (self.waiting_writers and owner not in self.readers and self.writer != owner)):
                self.condition.wait()
            self.readers[owner] = self.readers.get(owner, 0) + 1
        try:
            yield
        finally:
            with self.condition:
                self.readers[owner] -= 1
                if not self.readers[owner]: del self.readers[owner]
                self.condition.notify_all()

    @contextmanager
    def exclusive(self):
        owner = get_ident()
        with self.operation:
            with self.condition:
                if owner in self.readers:
                    raise RuntimeError("還原前請先關閉目前資料庫連線")
                self.waiting_writers += 1
                try:
                    while self.writer is not None or self.readers:
                        self.condition.wait()
                    self.writer = owner
                finally:
                    self.waiting_writers -= 1
            try:
                yield
            finally:
                with self.condition:
                    self.writer = None
                    self.condition.notify_all()


_locks = {}
_registry_lock = Lock()


def storage_lock(path: Path) -> StorageLock:
    key = str(path.resolve())
    with _registry_lock:
        if key not in _locks: _locks[key] = StorageLock()
        return _locks[key]

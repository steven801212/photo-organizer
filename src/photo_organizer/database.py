from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from .storage_lock import storage_lock


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    original_path TEXT NOT NULL,
    current_path TEXT NOT NULL,
    capture_date TEXT,
    file_type TEXT NOT NULL,
    extension TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    destination_path TEXT,
    sha256 TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    duplicate_of INTEGER REFERENCES photos(id),
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS imports_batch_idx ON imports(batch_id);
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(name)
);
CREATE TABLE IF NOT EXISTS album_photos (
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL,
    PRIMARY KEY(album_id, photo_id)
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS maintenance_runs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
"""

PHOTO_COLUMNS = {
    "favorite": "INTEGER NOT NULL DEFAULT 0",
    "deleted_at": "TEXT",
    "trash_original_path": "TEXT",
    "camera_make": "TEXT",
    "camera_model": "TEXT",
    "lens_model": "TEXT",
    "focal_length": "TEXT",
    "aperture": "TEXT",
    "shutter_speed": "TEXT",
    "iso": "TEXT",
    "gps_latitude": "REAL",
    "gps_longitude": "REAL",
    "metadata_indexed_at": "TEXT",
    "live_pair_key": "TEXT",
    "live_role": "TEXT",
    "live_partner_id": "INTEGER",
    "quick_hash": "TEXT",
    "content_identifier": "TEXT",
}


class Database:
    def __init__(self, path: Path):
        guard = storage_lock(path)
        self._lease = guard.read()
        self._lease.__enter__()
        self._closed = False
        try:
            with guard.schema:
                self._initialize(path)
        except BaseException:
            if hasattr(self, "connection"): self.connection.close()
            self._lease.__exit__(None, None, None)
            self._closed = True
            raise

    def _initialize(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.execute("PRAGMA busy_timeout=10000")
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(photos)")}
        for name, declaration in PHOTO_COLUMNS.items():
            if name not in columns:
                self.connection.execute(f"ALTER TABLE photos ADD COLUMN {name} {declaration}")
        self.connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES(1)")
        self.connection.commit()

    def close(self) -> None:
        if not self._closed:
            try: self.connection.close()
            finally:
                self._closed = True
                self._lease.__exit__(None, None, None)

    @contextmanager
    def transaction(self):
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def find_photo(self, digest: str):
        return self.connection.execute("SELECT * FROM photos WHERE sha256 = ?", (digest,)).fetchone()

    def duplicate_candidates(self, file_size: int, quick_hash: str):
        return self.connection.execute(
            "SELECT * FROM photos WHERE status='ACTIVE' AND file_size=? AND (quick_hash=? OR quick_hash IS NULL)",
            (file_size, quick_hash),
        ).fetchall()

    def find_rejected(self, digest: str):
        return self.connection.execute(
            """SELECT * FROM imports WHERE sha256=? AND action IN ('OTHER','OTHER_DUPLICATE')
               AND status IN ('COMPLETED','SOURCE_RETAINED') ORDER BY id DESC LIMIT 1""", (digest,)
        ).fetchone()

    def photo_by_id(self, photo_id: int):
        return self.connection.execute("SELECT * FROM photos WHERE id=? AND status='ACTIVE'", (photo_id,)).fetchone()

    def library_page(self, offset: int = 0, limit: int = 60, file_type: str | None = None,
                     year: str | None = None, day: str | None = None,
                     search: str | None = None, favorites: bool = False):
        where, params = "status='ACTIVE' AND COALESCE(live_role,'PHOTO')!='VIDEO'", []
        if file_type in {"RAW", "JPG", "VIDEO"}:
            where += " AND file_type=?"; params.append(file_type)
        elif file_type == "LIVE":
            where += " AND live_role='PHOTO'"
        if day == "日期未知" or year == "日期未知":
            where += " AND capture_date IS NULL"
        elif day and len(day) == 10:
            where += " AND substr(capture_date,1,10)=?"; params.append(day)
        elif year and len(year) == 4:
            where += " AND substr(capture_date,1,4)=?"; params.append(year)
        if search:
            where += " AND (lower(current_path) LIKE ? OR capture_date LIKE ? OR lower(COALESCE(camera_make,'')) LIKE ? OR lower(COALESCE(camera_model,'')) LIKE ? OR lower(COALESCE(lens_model,'')) LIKE ? OR COALESCE(iso,'') LIKE ? OR COALESCE(focal_length,'') LIKE ?)"
            needle = f"%{search.lower()}%"; params.extend((needle, f"%{search}%", needle, needle, needle, f"%{search}%", f"%{search}%"))
        if favorites:
            where += " AND favorite=1"
        total = self.connection.execute(f"SELECT COUNT(*) FROM photos WHERE {where}", params).fetchone()[0]
        rows = self.connection.execute(
            f"SELECT id,current_path,capture_date,file_type,extension,file_size,imported_at,favorite,live_pair_key,live_role,live_partner_id FROM photos WHERE {where} ORDER BY capture_date DESC,imported_at DESC,id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return total, rows

    def photos_by_ids(self, ids: list[int]):
        if not ids: return []
        ids = self.linked_ids(ids)
        placeholders = ",".join("?" for _ in ids)
        return self.connection.execute(
            f"SELECT * FROM photos WHERE status='ACTIVE' AND id IN ({placeholders})", ids
        ).fetchall()

    def linked_ids(self, ids: list[int]) -> list[int]:
        if not ids: return []
        output = set(ids); placeholders = ",".join("?" for _ in ids)
        for row in self.connection.execute(
            f"SELECT live_partner_id FROM photos WHERE id IN ({placeholders}) AND live_partner_id IS NOT NULL", ids
        ): output.add(int(row[0]))
        return sorted(output)

    def set_favorite(self, photo_id: int, favorite: bool) -> bool:
        cursor = self.connection.execute(
            "UPDATE photos SET favorite=? WHERE id=? AND status='ACTIVE'", (int(favorite), photo_id)
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def update_photo_metadata(self, photo_id: int, values: dict) -> None:
        self.connection.execute(
            """UPDATE photos SET camera_make=:camera_make, camera_model=:camera_model,
               lens_model=:lens_model, focal_length=:focal_length, aperture=:aperture,
               shutter_speed=:shutter_speed, iso=:iso, gps_latitude=:gps_latitude,
               gps_longitude=:gps_longitude, content_identifier=:content_identifier,
               metadata_indexed_at=:metadata_indexed_at
               WHERE id=:photo_id""", {**values, "photo_id": photo_id}
        )

    def trash_page(self, limit: int = 200, offset: int = 0):
        return self.connection.execute(
            "SELECT id,current_path,trash_original_path,capture_date,file_type,deleted_at FROM photos WHERE status='TRASH' ORDER BY deleted_at DESC,id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

    def albums(self):
        return self.connection.execute(
            """SELECT albums.id,albums.name,albums.description,albums.created_at,COUNT(photos.id) AS count
               FROM albums LEFT JOIN album_photos ON albums.id=album_photos.album_id
               LEFT JOIN photos ON photos.id=album_photos.photo_id AND photos.status='ACTIVE' AND COALESCE(photos.live_role,'PHOTO')!='VIDEO'
               GROUP BY albums.id ORDER BY albums.name"""
        ).fetchall()

    def create_album(self, name: str, description: str, created_at: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO albums(name,description,created_at) VALUES(?,?,?)", (name, description, created_at)
        ); self.connection.commit(); return int(cursor.lastrowid)

    def add_to_album(self, album_id: int, photo_ids: list[int], added_at: str) -> int:
        before = self.connection.total_changes
        self.connection.executemany(
            "INSERT OR IGNORE INTO album_photos(album_id,photo_id,added_at) VALUES(?,?,?)",
            ((album_id, photo_id, added_at) for photo_id in photo_ids),
        ); self.connection.commit(); return self.connection.total_changes - before

    def album_photos(self, album_id: int, offset: int = 0, limit: int = 60):
        total = self.connection.execute("""SELECT COUNT(*) FROM album_photos a JOIN photos p ON p.id=a.photo_id
            WHERE album_id=? AND p.status='ACTIVE' AND COALESCE(p.live_role,'PHOTO')!='VIDEO'""", (album_id,)).fetchone()[0]
        rows = self.connection.execute(
            """SELECT photos.id,photos.current_path,photos.capture_date,photos.file_type,photos.extension,
                      photos.file_size,photos.imported_at,photos.favorite
               FROM album_photos JOIN photos ON photos.id=album_photos.photo_id
               WHERE album_photos.album_id=? AND photos.status='ACTIVE' AND COALESCE(photos.live_role,'PHOTO')!='VIDEO'
               ORDER BY COALESCE(photos.capture_date,photos.imported_at) DESC,photos.id DESC LIMIT ? OFFSET ?""",
            (album_id, limit, offset),
        ).fetchall()
        return total, rows

    def settings(self):
        return {row["key"]: row["value"] for row in self.connection.execute("SELECT key,value FROM app_settings")}

    MAP_WHERE = """status='ACTIVE' AND COALESCE(live_role,'PHOTO')!='VIDEO'
                   AND gps_latitude BETWEEN -90 AND 90 AND gps_longitude BETWEEN -180 AND 180"""

    def map_count(self):
        return self.connection.execute(f"SELECT COUNT(*) FROM photos WHERE {self.MAP_WHERE}").fetchone()[0]

    def map_points(self, limit: int = 500, offset: int = 0):
        return self.connection.execute(
            f"""SELECT id,current_path,capture_date,file_type,gps_latitude,gps_longitude
               FROM photos WHERE {self.MAP_WHERE}
               ORDER BY COALESCE(capture_date,imported_at) DESC,id DESC LIMIT ? OFFSET ?""", (limit, offset)
        ).fetchall()

    def set_setting(self, key: str, value: str, updated_at: str):
        self.connection.execute(
            "INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (key, value, updated_at),
        ); self.connection.commit()

    def library_groups(self):
        return self.connection.execute(
            """SELECT COALESCE(substr(capture_date,1,4),'日期未知') AS year,
                      COALESCE(substr(capture_date,1,10),'日期未知') AS day,
                      COUNT(*) AS count
               FROM photos WHERE status='ACTIVE' AND COALESCE(live_role,'PHOTO')!='VIDEO'
               GROUP BY year,day ORDER BY (year='日期未知'),year DESC,day DESC"""
        ).fetchall()

    def recent_imports(self, limit: int = 100):
        return self.connection.execute(
            "SELECT batch_id,source_path,destination_path,action,status,error,created_at FROM imports ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def add_photo(self, **values) -> int:
        cursor = self.connection.execute(
            """INSERT INTO photos
            (sha256, original_path, current_path, capture_date, file_type, extension, file_size, imported_at, status)
            VALUES (:sha256, :original_path, :current_path, :capture_date, :file_type, :extension, :file_size, :imported_at, :status)""",
            values,
        )
        return int(cursor.lastrowid)

    def add_import(self, **values) -> int:
        cursor = self.connection.execute(
            """INSERT INTO imports
            (batch_id, source_path, destination_path, sha256, action, status, duplicate_of, error, created_at)
            VALUES (:batch_id, :source_path, :destination_path, :sha256, :action, :status, :duplicate_of, :error, :created_at)""",
            values,
        )
        return int(cursor.lastrowid)

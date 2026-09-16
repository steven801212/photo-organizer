from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import sqlite3

from photo_organizer.database import Database


class LibraryTests(unittest.TestCase):
    def test_library_newest_first_with_stable_pages_and_unknown_last(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "photos.db")
            try:
                dates = ["2012-01-01T12:00:00", None, "2026-09-08T12:00:00",
                         "2026-09-08T12:00:00", "2026-01-02T12:00:00"]
                ids = []
                with db.transaction():
                    for i, date in enumerate(dates):
                        ids.append(db.add_photo(
                            sha256=f"{i:064x}", original_path=f"/inbox/{i}.jpg",
                            current_path=f"/library/{i}.jpg", capture_date=date,
                            file_type="JPG", extension=".jpg", file_size=10,
                            imported_at="2026-09-08T15:00:00", status="ACTIVE"))
                paged_ids = []
                for offset in range(0, len(ids), 2):
                    total, rows = db.library_page(offset=offset, limit=2)
                    self.assertEqual(total, 5)
                    paged_ids.extend(row["id"] for row in rows)
                self.assertEqual(paged_ids, [ids[3], ids[2], ids[4], ids[0], ids[1]])
                groups = db.library_groups()
                self.assertEqual([row["day"] for row in groups],
                                 ["2026-09-08", "2026-01-02", "2012-01-01", "日期未知"])
                self.assertEqual(groups[0]["count"], 2)
                self.assertEqual(db.library_page(day="2026-01-02")[1][0]["id"], ids[4])
                self.assertEqual(db.library_page(year="日期未知")[1][0]["id"], ids[1])
            finally:
                db.close()

    def test_v02_database_is_upgraded_without_losing_photos(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "old.db"; connection = sqlite3.connect(path)
            connection.execute("""CREATE TABLE photos(id INTEGER PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,
                original_path TEXT NOT NULL,current_path TEXT NOT NULL,capture_date TEXT,file_type TEXT NOT NULL,
                extension TEXT NOT NULL,file_size INTEGER NOT NULL,imported_at TEXT NOT NULL,status TEXT NOT NULL)""")
            connection.execute("INSERT INTO photos VALUES(1,?,?,?,?,?,?,?,?,?)",
                               ("b"*64,"/old/a.jpg","/old/a.jpg","2020-01-01T00:00:00","JPG",".jpg",10,"now","ACTIVE"))
            connection.commit(); connection.close()
            db = Database(path)
            self.assertEqual(db.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(db.connection.execute("PRAGMA busy_timeout").fetchone()[0], 10000)
            columns = {row[1] for row in db.connection.execute("PRAGMA table_info(photos)")}
            self.assertIn("trash_original_path", columns); self.assertIn("camera_model", columns)
            self.assertEqual(db.connection.execute("SELECT COUNT(*) FROM photos").fetchone()[0], 1)
            db.close()

    def test_search_favorite_and_existing_database_migration(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "photos.db")
            with db.transaction():
                photo_id = db.add_photo(
                    sha256="a" * 64, original_path="/inbox/snow.ARW",
                    current_path="/library/2023/2023-02-04/snow.ARW",
                    capture_date="2023-02-04T11:56:00", file_type="RAW", extension=".ARW",
                    file_size=123, imported_at=datetime.now(timezone.utc).isoformat(), status="ACTIVE",
                )
            total, _ = db.library_page(search="snow")
            self.assertEqual(total, 1)
            self.assertTrue(db.set_favorite(photo_id, True))
            total, rows = db.library_page(favorites=True)
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["favorite"], 1)
            self.assertEqual(db.library_groups()[0]["day"], "2023-02-04")
            album_id = db.create_album("冬天", "測試相簿", datetime.now(timezone.utc).isoformat())
            self.assertEqual(db.add_to_album(album_id, [photo_id], datetime.now(timezone.utc).isoformat()), 1)
            total, album_rows = db.album_photos(album_id)
            self.assertEqual(total, 1)
            self.assertEqual(album_rows[0]["id"], photo_id)
            db.connection.execute("UPDATE photos SET gps_latitude=?,gps_longitude=? WHERE id=?", (25.0330, 121.5654, photo_id))
            map_rows = db.map_points()
            self.assertEqual(len(map_rows), 1)
            self.assertEqual(map_rows[0]["id"], photo_id)
            db.close()

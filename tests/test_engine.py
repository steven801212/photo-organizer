from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime
from unittest.mock import patch
import unittest

from photo_organizer.config import Config
from photo_organizer.engine import Organizer, quick_hash_file, restore_index_backup, safe_filename, sha256_file, unique_live_paths, unique_path


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = Config(root / "Inbox", root / "Library", filesystem_date_fallback=True,
                             stable_interval=0, stable_checks=1)
        self.organizer = Organizer(self.config)

    def tearDown(self):
        self.organizer.close()
        self.temp.cleanup()

    def add(self, name="photo.jpg", content=b"photo-data"):
        path = self.config.inbox / name
        path.write_bytes(content)
        return path

    def test_import_and_undo(self):
        source = self.add()
        items = self.organizer.scan()
        self.assertEqual(items[0].action, "IMPORT")
        manifest = self.organizer.execute(items)
        destination = Path(items[0].destination)
        self.assertTrue(destination.exists())
        self.assertFalse(source.exists())
        self.assertEqual(sha256_file(destination), items[0].sha256)
        restored, failed = self.organizer.undo(manifest)
        self.assertEqual((restored, failed), (1, 0))
        self.assertTrue(source.exists())

    def test_duplicate_is_quarantined(self):
        first = self.add("one.jpg", b"same")
        items = self.organizer.scan()
        self.organizer.execute(items)
        second = self.add("two.jpg", b"same")
        planned = self.organizer.plan_file(second)
        self.assertEqual(planned.action, "DUPLICATE")
        self.assertIn("_Duplicates", planned.destination)

    def test_recycle_and_restore_never_delete_the_only_copy(self):
        self.add("keep.jpg", b"keep-me")
        items = self.organizer.scan(); self.organizer.execute(items)
        row = self.organizer.db.find_photo(items[0].sha256)
        original = Path(row["current_path"])
        self.assertEqual(self.organizer.move_to_trash([row["id"]]), (1, 0))
        trashed = self.organizer.db.connection.execute("SELECT * FROM photos WHERE id=?", (row["id"],)).fetchone()
        self.assertEqual(trashed["status"], "TRASH")
        self.assertTrue(Path(trashed["current_path"]).is_file())
        self.assertFalse(original.exists())
        self.assertEqual(self.organizer.restore_from_trash([row["id"]]), (1, 0))
        restored = self.organizer.db.photo_by_id(row["id"])
        self.assertTrue(Path(restored["current_path"]).is_file())

    def test_health_check_finds_missing_and_unindexed_files(self):
        self.add("indexed.jpg", b"indexed")
        items = self.organizer.scan(); self.organizer.execute(items)
        indexed = Path(items[0].destination); indexed.unlink()
        extra = self.config.jpg_dir / "extra.jpg"; extra.write_bytes(b"extra")
        result = self.organizer.health_check()
        self.assertEqual(result["missing_count"], 1)
        self.assertEqual(result["unindexed_count"], 1)

    def test_verified_backup_restore_preserves_current_safety_copy(self):
        self.add("first.jpg", b"first")
        self.organizer.execute(self.organizer.scan())
        backup = self.organizer.backup_index()
        self.add("second.jpg", b"second")
        self.organizer.execute(self.organizer.scan())
        self.assertEqual(self.organizer.db.connection.execute("SELECT COUNT(*) FROM photos").fetchone()[0], 2)
        self.organizer.close()
        safety = restore_index_backup(self.config, backup)
        self.assertTrue(safety.is_file())
        self.organizer = Organizer(self.config)
        self.assertEqual(self.organizer.db.connection.execute("SELECT COUNT(*) FROM photos").fetchone()[0], 1)

    def test_duplicate_inside_one_batch_is_quarantined(self):
        self.add("one.jpg", b"same-batch")
        self.add("two.jpg", b"same-batch")
        items = self.organizer.scan()
        self.assertEqual([item.action for item in items], ["IMPORT", "DUPLICATE"])
        self.organizer.execute(items)
        self.assertTrue(Path(items[0].destination).exists())
        self.assertTrue(Path(items[1].destination).exists())

    def test_new_jpg_mov_live_photo_stays_together_and_operates_as_pair(self):
        self.add("IMG_1001.JPG", b"still")
        self.add("IMG_1001.MOV", b"motion")
        items = self.organizer.scan(); photo = next(x for x in items if x.source.endswith(".JPG")); video = next(x for x in items if x.source.endswith(".MOV"))
        self.assertIn("LIVE", photo.destination); self.assertEqual(Path(photo.destination).parent, Path(video.destination).parent)
        self.assertEqual(Path(photo.destination).stem, Path(video.destination).stem)
        self.organizer.execute(items)
        rows = self.organizer.db.connection.execute("SELECT * FROM photos ORDER BY id").fetchall()
        still = next(row for row in rows if row["live_role"] == "PHOTO")
        motion = next(row for row in rows if row["live_role"] == "VIDEO")
        self.assertEqual(still["live_partner_id"], motion["id"]); self.assertEqual(motion["live_partner_id"], still["id"])
        self.assertEqual(len(self.organizer.db.photos_by_ids([still["id"]])), 2)
        self.assertEqual(self.organizer.move_to_trash([still["id"]]), (2, 0))
        self.assertEqual(self.organizer.restore_from_trash([still["id"]]), (2, 0))

    def test_old_separated_live_photo_can_be_previewed_and_migrated(self):
        jpg = self.config.jpg_dir / "2020" / "2020-01-01" / "IMG_OLD.JPG"
        mov = self.config.video_dir / "2020" / "2020-01-01" / "IMG_OLD.MOV"
        jpg.parent.mkdir(parents=True); mov.parent.mkdir(parents=True); jpg.write_bytes(b"old-still"); mov.write_bytes(b"old-motion")
        self.organizer.reindex(); self.assertEqual(len(self.organizer.live_migration_plan()), 1)
        result = self.organizer.migrate_live_photos(); self.assertEqual(result["completed"], 1)
        rows = self.organizer.db.connection.execute("SELECT current_path FROM photos").fetchall()
        self.assertTrue(all("LIVE" in row["current_path"] for row in rows))

    def test_collision_never_overwrites(self):
        folder = self.config.jpg_dir
        folder.mkdir(exist_ok=True)
        original = folder / "x.jpg"
        original.write_bytes(b"original")
        self.assertEqual(unique_path(original).name, "x_001.jpg")
        self.assertEqual(original.read_bytes(), b"original")

    def test_nested_paths_are_rejected(self):
        root = Path(self.temp.name)
        with self.assertRaises(ValueError):
            Config(root, root / "Library").validate()

    def test_reorganize_unsorted_and_undo(self):
        source = self.config.unsorted_dir / "old.jpg"
        source.write_bytes(b"dated-photo")
        digest = sha256_file(source)
        with self.organizer.db.transaction():
            self.organizer.db.add_photo(
                sha256=digest, original_path=str(source), current_path=str(source), capture_date=None,
                file_type="JPG", extension=".jpg", file_size=source.stat().st_size,
                imported_at="2026-01-01T00:00:00+00:00", status="ACTIVE",
            )
        with patch("photo_organizer.engine.capture_date", return_value=(datetime(2020, 2, 3, 4, 5, 6), "DateTimeOriginal")):
            moved, unchanged = self.organizer.reorganize_unsorted()
        self.assertEqual((moved, unchanged), (1, 0))
        destination = self.config.jpg_dir / "2020/2020-02-03/old.jpg"
        self.assertTrue(destination.exists())
        restored, failed = self.organizer.undo()
        self.assertEqual((restored, failed), (1, 0))
        self.assertTrue(source.exists())

    def test_reindex_repairs_moved_library_path(self):
        old = self.config.jpg_dir / "2020/2020-01-01/photo.jpg"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b"moved-library-photo")
        self.organizer.reindex()
        digest = sha256_file(old)
        new = self.config.jpg_dir / "2021/2021-01-01/photo.jpg"
        new.parent.mkdir(parents=True, exist_ok=True)
        old.replace(new)
        added, skipped = self.organizer.reindex()
        self.assertEqual((added, skipped), (0, 1))
        self.assertEqual(self.organizer.db.find_photo(digest)["current_path"], str(new))

    def test_video_is_supported(self):
        source = self.add("clip.mp4", b"test-video")
        item = self.organizer.plan_file(source)
        self.assertEqual(item.file_type, "VIDEO")
        self.assertIn("VIDEO", item.destination)

    def test_xmp_follows_raw_and_undo(self):
        raw = self.add("image.arw", b"raw-content")
        xmp = self.add("image.xmp", b"xmp-content")
        items = self.organizer.scan()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].companions[0]["source"], str(xmp))
        manifest = self.organizer.execute(items)
        raw_destination = Path(items[0].destination)
        xmp_destination = raw_destination.with_suffix(".xmp")
        self.assertTrue(raw_destination.exists())
        self.assertTrue(xmp_destination.exists())
        restored, failed = self.organizer.undo(manifest)
        self.assertEqual((restored, failed), (1, 0))
        self.assertTrue(raw.exists())
        self.assertTrue(xmp.exists())

    def test_other_files_are_quarantined_and_empty_folders_removed(self):
        nested = self.config.inbox / "documents" / "old"
        nested.mkdir(parents=True)
        source = nested / "manual.pdf"; source.write_bytes(b"not-a-photo")
        items = self.organizer.scan()
        self.assertEqual(len(items), 1); self.assertEqual(items[0].action, "OTHER")
        self.assertEqual(items[0].file_type, "OTHER")
        self.organizer.execute(items)
        self.assertTrue(Path(items[0].destination).is_file())
        self.assertFalse(nested.exists())
        self.assertTrue(self.config.inbox.is_dir())
        self.assertEqual(self.organizer.db.connection.execute("SELECT COUNT(*) FROM photos").fetchone()[0], 0)

    def test_duplicate_other_files_are_kept_in_rejected_duplicates(self):
        first = self.add("first.pdf", b"same-document")
        first_item = self.organizer.scan()[0]; self.organizer.execute([first_item])
        second = self.add("second.pdf", b"same-document")
        second_item = self.organizer.scan()[0]
        self.assertEqual(second_item.action, "OTHER_DUPLICATE")
        self.assertIn("_Duplicates", second_item.destination)
        self.organizer.execute([second_item])
        self.assertTrue(Path(first_item.destination).is_file())
        self.assertTrue(Path(second_item.destination).is_file())

    def test_scan_reports_reading_progress(self):
        self.add("photo.jpg", b"photo")
        self.add("notes.txt", b"notes")
        events = []
        self.organizer.scan(lambda message, current, total, phase: events.append((current, total, phase)))
        self.assertIn((2, 2, "preview"), events)

    def test_synology_and_desktop_metadata_are_ignored(self):
        eadir = self.config.inbox / "album" / "@eaDir"; eadir.mkdir(parents=True)
        (eadir / "SYNOPHOTO_THUMB_XL.jpg").write_bytes(b"system-thumbnail")
        (self.config.inbox / ".DS_Store").write_bytes(b"finder")
        (self.config.inbox / "Thumbs.db").write_bytes(b"windows")
        self.add("real.jpg", b"real")
        items = self.organizer.scan()
        self.assertEqual([Path(item.source).name for item in items], ["real.jpg"])
        self.organizer.execute(items)
        self.assertTrue((eadir / "SYNOPHOTO_THUMB_XL.jpg").is_file())
        self.assertTrue((self.config.inbox / ".DS_Store").is_file())

    def test_quick_hash_defers_full_hash_when_no_candidate_exists(self):
        source = self.add("unique.jpg", b"unique-content")
        with patch("photo_organizer.engine.sha256_file") as full_hash:
            item = self.organizer.plan_file(source)
        full_hash.assert_not_called(); self.assertEqual(item.sha256, "")
        self.assertEqual(item.quick_hash, quick_hash_file(source))

    def test_filename_is_nfc_and_windows_safe(self):
        self.assertEqual(safe_filename("bad:name?.jpg"), "bad_name_.jpg")
        self.assertEqual(safe_filename("CON.txt"), "_CON.txt")
        self.assertEqual(safe_filename("e\u0301.jpg"), "é.jpg")

    def test_metadata_is_read_outside_sqlite_write_transaction(self):
        self.add("metadata.jpg", b"metadata")
        item = self.organizer.scan()[0]
        def inspect_transaction(path):
            self.assertFalse(self.organizer.db.connection.in_transaction)
            return {"camera_make": None, "camera_model": None, "lens_model": None,
                    "focal_length": None, "aperture": None, "shutter_speed": None,
                    "iso": None, "gps_latitude": None, "gps_longitude": None,
                    "content_identifier": None}
        with patch("photo_organizer.engine.index_metadata", side_effect=inspect_transaction):
            self.organizer.execute([item])
        self.assertTrue(Path(item.destination).is_file())


if __name__ == "__main__":
    unittest.main()

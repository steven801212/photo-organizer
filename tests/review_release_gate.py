"""Release review probes. Run explicitly; all fixtures live in temporary folders."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from photo_organizer.config import Config
from photo_organizer.engine import Organizer, sha256_file


METADATA = dict.fromkeys(("camera_make", "camera_model", "lens_model", "focal_length",
                         "aperture", "shutter_speed", "iso", "gps_latitude",
                         "gps_longitude", "content_identifier"))


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="photo-review-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.organizer = Organizer(Config(root / "Inbox", root / "Library",
                                          stable_interval=0, stable_checks=1))
        self.addCleanup(self.organizer.close)
        for target, value in (("capture_date", (datetime(2023, 2, 4, 12), "EXIF")),
                              ("index_metadata", METADATA), ("media_group_identifier", None)):
            mock = patch("photo_organizer.engine." + target, return_value=value)
            mock.start(); self.addCleanup(mock.stop)

    def add(self, name, content):
        path = self.organizer.config.inbox / name
        path.write_bytes(content)
        return path

    def test_existing_xmp_is_never_overwritten(self):
        source = self.add("photo.arw", b"raw")
        self.add("photo.xmp", b"new edits")
        items = self.organizer.scan()
        item = next(i for i in items if i.source == str(source))
        target = Path(item.destination).with_suffix(".xmp")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"existing valuable edits")
        self.organizer.execute(items)
        self.assertEqual(target.read_bytes(), b"existing valuable edits")

    def test_distinct_full_hashes_do_not_enter_duplicate_folder(self):
        head, tail = b"h" * 65536, b"t" * 65536
        a = self.add("a.jpg", head + b"A" * 64 + tail)
        b = self.add("b.jpg", head + b"B" * 64 + tail)
        self.assertNotEqual(sha256_file(a), sha256_file(b))
        items = self.organizer.scan()
        self.assertTrue(all(i.action == "IMPORT" for i in items))
        self.assertTrue(all("_Duplicates" not in Path(i.destination).parts for i in items))

    def test_changed_unhashed_source_requires_fresh_preview(self):
        source = self.add("changed.jpg", b"initial image")
        items = self.organizer.scan()
        self.assertEqual(items[0].sha256, "")
        source.write_bytes(b"different content after preview, possibly a new capture date")
        self.organizer.execute(items)
        self.assertTrue(source.exists(), "Changed source was moved using stale preview metadata")
        self.assertEqual(items[0].status, "FAILED")

    def test_identifier_takes_priority_over_conflicting_stem(self):
        self.add("photo.jpg", b"photo A")
        self.add("photo.mov", b"video B")
        self.add("matching.mov", b"video A")
        with patch("photo_organizer.engine.media_group_identifier",
                   side_effect=lambda p: "B" if p.name == "photo.mov" else "A"):
            items = self.organizer.scan()
        by_name = {Path(i.source).name: i for i in items}
        self.assertEqual(by_name["matching.mov"].live_role, "VIDEO")
        self.assertIsNone(by_name["photo.mov"].live_role)


if __name__ == "__main__":
    unittest.main(verbosity=2)

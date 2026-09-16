from pathlib import Path
from unittest.mock import patch
import unittest

from photo_organizer.metadata import capture_date


class MetadataTests(unittest.TestCase):
    def test_filename_date_is_used_without_timezone_conversion(self):
        with patch("photo_organizer.metadata.exiftool_path", return_value=None):
            value, source = capture_date(Path("IMG_20230204_123000.jpg"))
        self.assertEqual(value.isoformat(), "2023-02-04T12:30:00")
        self.assertEqual(source, "FileName")

    def test_invalid_filename_date_is_not_guessed(self):
        with patch("photo_organizer.metadata.exiftool_path", return_value=None):
            value, source = capture_date(Path("IMG_20239999_999999.jpg"))
        self.assertIsNone(value); self.assertEqual(source, "NO_CAPTURE_DATE")


if __name__ == "__main__":
    unittest.main()

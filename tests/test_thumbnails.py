from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
from pillow_heif import from_pillow

from photo_organizer.thumbnails import thumbnail


class ThumbnailTests(unittest.TestCase):
    def test_jpeg_thumbnail_is_bounded_and_cached(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); source = root / "photo.jpg"; cache = root / "cache" / "1.jpg"
            Image.new("RGB", (1200, 800), "navy").save(source)
            result = thumbnail(source, cache, size=240)
            self.assertEqual(result, cache)
            with Image.open(result) as image:
                self.assertEqual(image.size, (240, 240))
            first_mtime = result.stat().st_mtime_ns
            thumbnail(source, cache, size=240)
            self.assertEqual(result.stat().st_mtime_ns, first_mtime)

    def test_heic_thumbnail_decodes_original_pixels(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); source = root / "photo.heic"; cache = root / "cache" / "2.jpg"
            from_pillow(Image.new("RGB", (640, 360), (210, 35, 45))).save(source, quality=90)
            thumbnail(source, cache, size=240)
            with Image.open(cache) as image:
                self.assertEqual(image.size, (240, 240))
                red, green, blue = image.getpixel((120, 120))
                self.assertGreater(red, 150)
                self.assertLess(green, 90)
                self.assertLess(blue, 100)

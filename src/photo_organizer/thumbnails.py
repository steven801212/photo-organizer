from __future__ import annotations

from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore
import os
import subprocess

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

try:
    from pillow_heif import register_heif_opener
except ImportError:  # Allows a clear fallback outside the packaged Docker image.
    register_heif_opener = None
else:
    register_heif_opener()

from .metadata import _creation_flags, exiftool_path

THUMBNAIL_SLOTS = BoundedSemaphore(max(1, min(4, int(os.environ.get("THUMBNAIL_WORKERS", "1")))))


def _preview_bytes(path: Path) -> bytes | None:
    executable = exiftool_path()
    if not executable:
        return None
    for tag in ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage"):
        result = subprocess.run([str(executable), "-b", tag, str(path)], capture_output=True,
                                timeout=45, creationflags=_creation_flags(), check=False)
        if result.returncode == 0 and len(result.stdout) > 100:
            return result.stdout
    return None


def _thumbnail(path: Path, cache_path: Path, size: int = 480) -> Path:
    if cache_path.exists() and cache_path.stat().st_mtime >= path.stat().st_mtime:
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    preview = _preview_bytes(path)
    source = BytesIO(preview) if preview else path
    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            profile = image.info.get("icc_profile")
            if profile:
                try:
                    image = ImageCms.profileToProfile(
                        image, ImageCms.ImageCmsProfile(BytesIO(profile)),
                        ImageCms.createProfile("sRGB"), outputMode="RGB"
                    )
                except (ImageCms.PyCMSError, OSError, TypeError):
                    image = image.convert("RGB")
            else:
                image = image.convert("RGB")
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (size, size), "#ececf0")
            canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2))
    except (UnidentifiedImageError, OSError):
        canvas = Image.new("RGB", (size, size), "#d9d9df")
    temporary = cache_path.with_suffix(".jpg.partial")
    canvas.save(temporary, "JPEG", quality=82, optimize=True)
    temporary.replace(cache_path)
    return cache_path


def thumbnail(path: Path, cache_path: Path, size: int = 480) -> Path:
    """Generate one preview at a time by default to protect low-memory NAS devices."""
    with THUMBNAIL_SLOTS:
        return _thumbnail(path, cache_path, size)

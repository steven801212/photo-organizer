from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Lock, Timer, Event
from functools import lru_cache
import atexit
import json
import os
import shutil
import subprocess
import sys
import re


DATE_FIELDS = ("DateTimeOriginal", "CreateDate", "DateCreated", "MediaCreateDate")
FILENAME_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)[ T_-]?([0-2]\d)?[._-]?([0-5]\d)?[._-]?([0-5]\d)?(?!\d)"),
)


class ExifToolSession:
    """A serialized ExifTool stay-open pipe shared by metadata readers."""
    def __init__(self):
        self.lock = Lock(); self.process = None; self.sequence = 0

    def _start(self):
        executable = exiftool_path()
        if not executable: raise FileNotFoundError("ExifTool unavailable")
        self.process = subprocess.Popen(
            [str(executable), "-stay_open", "True", "-@", "-"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_creation_flags(),
        )

    def json(self, path: Path, tags: tuple[str, ...], numeric: bool = False, timeout: float = 30) -> list[dict]:
        if any(c in str(path) for c in "\r\n"):
            raise ValueError("ExifTool 管道不接受含換行的路徑")
        with self.lock:
            if not self.process or self.process.poll() is not None: self._start()
            self.sequence += 1; marker = str(self.sequence)
            arguments = ["-json", "-charset", "filename=UTF8"]
            if numeric: arguments.append("-n")
            arguments.extend(f"-{tag}" for tag in tags); arguments.extend((str(path), f"-execute{marker}"))
            payload = ("\n".join(arguments) + "\n").encode("utf-8")
            process = self.process
            expired = Event()
            def abort():
                expired.set()
                try: process.kill()
                except OSError: pass
            watchdog = Timer(timeout, abort)
            watchdog.daemon = True
            watchdog.start()
            try:
                self.process.stdin.write(payload); self.process.stdin.flush()
                output = bytearray(); ready = f"{{ready{marker}}}".encode()
                while True:
                    line = self.process.stdout.readline()
                    if not line:
                        if expired.is_set(): raise TimeoutError("ExifTool 讀取逾時，請檢查檔案")
                        raise OSError("ExifTool stay-open pipe closed")
                    if line.strip() == ready: break
                    output.extend(line)
                    if len(output) > 8 * 1024 * 1024: raise ValueError("ExifTool 回應過大")
                if expired.is_set(): raise TimeoutError("ExifTool 讀取逾時")
                return json.loads(output.decode("utf-8", "replace") or "[]")
            except Exception:
                self.close(); raise
            finally:
                watchdog.cancel(); watchdog.join(timeout=1)

    def close(self):
        process, self.process = self.process, None
        if not process: return
        try:
            if process.poll() is None:
                process.stdin.write(b"-stay_open\nFalse\n"); process.stdin.flush(); process.wait(timeout=3)
        except Exception:
            process.kill()
        finally:
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired: pass
            for stream in (process.stdin, process.stdout):
                try:
                    if stream: stream.close()
                except OSError: pass


EXIFTOOL = ExifToolSession()
atexit.register(EXIFTOOL.close)


def _metadata_json(path: Path, tags: tuple[str, ...], numeric: bool = False) -> list[dict]:
    try:
        return EXIFTOOL.json(path, tags, numeric)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        executable = exiftool_path()
        if not executable: return []
        command = [str(executable), "-json"]
        if numeric: command.append("-n")
        command.extend(f"-{tag}" for tag in tags); command.append(str(path))
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=30, check=False, creationflags=_creation_flags())
        return json.loads(result.stdout or "[]") if result.returncode == 0 else []


def exiftool_path() -> Path | None:
    """Find the bundled portable ExifTool before checking PATH."""
    roots = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.append(Path(bundle_root))
    roots.extend((Path(__file__).resolve().parents[2], Path(sys.executable).resolve().parent))
    relative_candidates = (
        Path("vendor/exiftool/exiftool-13.59_64/exiftool.exe"),
        Path("vendor/exiftool/exiftool.exe"),
        Path("exiftool/exiftool.exe"),
    )
    for root in roots:
        for relative in relative_candidates:
            candidate = root / relative
            if candidate.is_file():
                return candidate
    system = shutil.which("exiftool")
    return Path(system) if system else None


@lru_cache(maxsize=1)
def exiftool_version() -> str | None:
    executable = exiftool_path()
    if not executable:
        return None
    try:
        result = subprocess.run([str(executable), "-ver"], capture_output=True, text=True,
                                timeout=30, check=True, creationflags=_creation_flags())
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _parse_date(value: str) -> datetime | None:
    value = value.strip()
    if len(value) >= 19:
        value = value[:19].replace(":", "-", 2)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def capture_date(path: Path, allow_filesystem_fallback: bool = False) -> tuple[datetime | None, str]:
    executable = exiftool_path()
    if executable:
        records = _metadata_json(path, DATE_FIELDS)
        if records:
            for field in DATE_FIELDS:
                parsed = _parse_date(str(records[0].get(field, "")))
                if parsed:
                    return parsed, field
    for pattern in FILENAME_DATE_PATTERNS:
        match = pattern.search(path.stem)
        if not match: continue
        year, month, day, hour, minute, second = match.groups()
        try: return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0)), "FileName"
        except ValueError: continue
    if allow_filesystem_fallback:
        return datetime.fromtimestamp(path.stat().st_mtime), "FileModifyDate"
    return None, "NO_CAPTURE_DATE"


def exif_details(path: Path) -> dict[str, str]:
    """Return a small, display-ready set of useful camera metadata."""
    executable = exiftool_path()
    if not executable:
        return {}
    tags = (
        "DateTimeOriginal", "Make", "Model", "LensModel", "LensID", "FocalLength",
        "FNumber", "ExposureTime", "ShutterSpeed", "ISO", "ExposureCompensation",
        "Flash", "ImageWidth", "ImageHeight", "GPSPosition", "GPSAltitude",
    )
    records = _metadata_json(path, tags)
    if not records:
        return {}
    record = records[0]
    return {tag: str(record[tag]) for tag in tags if record.get(tag) not in (None, "")}


def index_metadata(path: Path) -> dict[str, object | None]:
    """Read normalized metadata fields used by fast library search and maps."""
    executable = exiftool_path()
    empty = {"camera_make": None, "camera_model": None, "lens_model": None,
             "focal_length": None, "aperture": None, "shutter_speed": None,
             "iso": None, "gps_latitude": None, "gps_longitude": None, "content_identifier": None}
    if not executable:
        return empty
    tags = ("Make", "Model", "LensModel", "LensID", "FocalLength", "FNumber",
            "ExposureTime", "ShutterSpeed", "ISO", "GPSLatitude", "GPSLongitude",
            "ContentIdentifier", "MediaGroupUUID")
    records = _metadata_json(path, tags, numeric=True)
    if not records:
        return empty
    row = records[0]
    def text_value(*names):
        value = next((row.get(name) for name in names if row.get(name) not in (None, "")), None)
        return str(value) if value is not None else None
    def float_value(name):
        try: return float(row[name])
        except (KeyError, TypeError, ValueError): return None
    return {"camera_make": text_value("Make"), "camera_model": text_value("Model"),
            "lens_model": text_value("LensModel", "LensID"), "focal_length": text_value("FocalLength"),
            "aperture": text_value("FNumber"), "shutter_speed": text_value("ExposureTime", "ShutterSpeed"),
            "iso": text_value("ISO"), "gps_latitude": float_value("GPSLatitude"),
            "gps_longitude": float_value("GPSLongitude"),
            "content_identifier": text_value("ContentIdentifier", "MediaGroupUUID")}


def media_group_identifier(path: Path) -> str | None:
    if path.suffix.lower() not in {".jpg", ".jpeg", ".heic", ".heif", ".mov"}: return None
    records = _metadata_json(path, ("ContentIdentifier", "MediaGroupUUID"))
    if not records: return None
    value = records[0].get("ContentIdentifier") or records[0].get("MediaGroupUUID")
    return str(value).strip() if value else None

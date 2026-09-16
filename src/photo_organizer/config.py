from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tomllib


RAW_EXTENSIONS = frozenset({".arw", ".rw2", ".cr2", ".cr3", ".nef", ".nrw", ".orf", ".raf", ".dng"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".heif"})
VIDEO_EXTENSIONS = frozenset({".mov", ".mp4"})
SUPPORTED_EXTENSIONS = RAW_EXTENSIONS | IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
IGNORED_DIRECTORY_NAMES = frozenset({"@eadir", "@tmp", "#recycle", ".snapshot"})
IGNORED_FILE_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})


def ignored_path(path: Path) -> bool:
    if any(part.casefold() in IGNORED_DIRECTORY_NAMES for part in path.parts):
        return True
    name = path.name.casefold()
    return (name in IGNORED_FILE_NAMES or name.startswith("._") or
            name.endswith((".tmp", ".partial", ".crdownload")) or
            (name.startswith("~$") and len(name) > 2))


def default_config_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PhotoOrganizer"
    return base / "config.toml"


@dataclass(frozen=True)
class Config:
    inbox: Path
    library: Path
    filesystem_date_fallback: bool = False
    stable_interval: float = 2.0
    stable_checks: int = 2
    index_dir: Path | None = None
    backup_dir: Path | None = None
    reject_dir: Path | None = None

    @property
    def raw_dir(self) -> Path:
        return self.library / "RAW"

    @property
    def jpg_dir(self) -> Path:
        return self.library / "JPG"

    @property
    def duplicate_dir(self) -> Path:
        return self.library / "_Duplicates"

    @property
    def video_dir(self) -> Path:
        return self.library / "VIDEO"

    @property
    def live_dir(self) -> Path:
        return self.library / "LIVE"

    @property
    def unsorted_dir(self) -> Path:
        return self.library / "_Unsorted"

    @property
    def trash_dir(self) -> Path:
        return self.library / "_Trash"

    @property
    def system_dir(self) -> Path:
        return self.index_dir if self.index_dir is not None else self.library / "_System"

    @property
    def db_path(self) -> Path:
        return self.system_dir / "photo-organizer.db"

    @property
    def manifests_dir(self) -> Path:
        return self.system_dir / "manifests"

    @property
    def backups_dir(self) -> Path:
        return self.backup_dir if self.backup_dir is not None else self.system_dir / "backups"

    @property
    def rejected_dir(self) -> Path:
        return self.reject_dir if self.reject_dir is not None else self.system_dir / "rejected"

    @property
    def rejected_duplicate_dir(self) -> Path:
        return self.rejected_dir / "_Duplicates"

    def validate(self) -> None:
        inbox = self.inbox.resolve()
        library = self.library.resolve()
        if inbox == library or inbox in library.parents or library in inbox.parents:
            raise ValueError("Inbox 與 Photo Library 不可相同，也不可互相包含。")

    def ensure_directories(self) -> None:
        self.validate()
        self.inbox.mkdir(parents=True, exist_ok=True)
        for path in (self.raw_dir, self.jpg_dir, self.video_dir, self.live_dir, self.duplicate_dir, self.unsorted_dir, self.trash_dir, self.manifests_dir, self.backups_dir, self.rejected_dir, self.rejected_duplicate_dir):
            path.mkdir(parents=True, exist_ok=True)

    def same_storage_device(self) -> bool | None:
        try: return self.inbox.stat().st_dev == self.library.stat().st_dev
        except OSError: return None


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return Config(
        inbox=Path(data["paths"]["inbox"]),
        library=Path(data["paths"]["library"]),
        filesystem_date_fallback=bool(data.get("metadata", {}).get("filesystem_date_fallback", False)),
        stable_interval=float(data.get("stability", {}).get("interval", 2.0)),
        stable_checks=int(data.get("stability", {}).get("checks", 2)),
        index_dir=Path(data["paths"]["index_dir"]) if data["paths"].get("index_dir") else None,
        backup_dir=Path(data["paths"]["backup_dir"]) if data["paths"].get("backup_dir") else None,
        reject_dir=Path(data["paths"]["reject_dir"]) if data["paths"].get("reject_dir") else None,
    )


def save_config(config: Config, path: Path | None = None) -> Path:
    config.validate()
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    esc = lambda value: str(value).replace("\\", "\\\\").replace('"', '\\"')
    text = (
        f'[paths]\ninbox = "{esc(config.inbox)}"\nlibrary = "{esc(config.library)}"\n'
        f'index_dir = "{esc(config.system_dir)}"\n\n'
        f'backup_dir = "{esc(config.backups_dir)}"\n\n'
        f'reject_dir = "{esc(config.rejected_dir)}"\n\n'
        f'[metadata]\nfilesystem_date_fallback = {str(config.filesystem_date_fallback).lower()}\n\n'
        f'[stability]\ninterval = {config.stable_interval}\nchecks = {config.stable_checks}\n'
    )
    path.write_text(text, encoding="utf-8")
    return path

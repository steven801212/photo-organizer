from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
import hashlib
import json
import os
import shutil
import time
import uuid
import sqlite3
import re
import unicodedata
from .storage_lock import storage_lock

from .config import Config, IMAGE_EXTENSIONS, RAW_EXTENSIONS, SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS, ignored_path
from .database import Database
from .metadata import capture_date, index_metadata, media_group_identifier


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def quick_hash_file(path: Path, sample_size: int = 64 * 1024) -> str:
    """Candidate fingerprint only; final duplicate decisions still use SHA-256."""
    size = path.stat().st_size; digest = hashlib.blake2b(digest_size=16)
    digest.update(size.to_bytes(8, "big"))
    with path.open("rb") as handle:
        digest.update(handle.read(sample_size))
        if size > sample_size:
            handle.seek(max(0, size - sample_size)); digest.update(handle.read(sample_size))
    return digest.hexdigest()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(1, 100_000):
        candidate = path.with_name(f"{path.stem}_{number:03d}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"無法為檔案產生安全名稱：{path.name}")


WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{x}" for x in range(1, 10)), *(f"lpt{x}" for x in range(1, 10))}


def safe_filename(name: str) -> str:
    normalized = unicodedata.normalize("NFC", name)
    normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", normalized).rstrip(" .")
    if not normalized or normalized in {".", ".."}: normalized = "unnamed"
    path = Path(normalized)
    if path.stem.casefold() in WINDOWS_RESERVED: normalized = "_" + normalized
    if len(normalized) > 240:
        suffix = Path(normalized).suffix[:20]
        normalized = normalized[:240 - len(suffix)] + suffix
    return normalized


def safe_relative(path: Path) -> Path:
    return Path(*(safe_filename(part) for part in path.parts))


def unique_live_paths(photo: Path, video: Path) -> tuple[Path, Path]:
    if not photo.exists() and not video.exists(): return photo, video
    for number in range(1, 100_000):
        stem = f"{photo.stem}_{number:03d}"
        photo_candidate, video_candidate = photo.with_name(stem + photo.suffix), video.with_name(stem + video.suffix)
        if not photo_candidate.exists() and not video_candidate.exists(): return photo_candidate, video_candidate
    raise RuntimeError(f"無法為 Live Photo 產生安全名稱：{photo.stem}")


def stable(path: Path, interval: float, checks: int) -> bool:
    previous = None
    for attempt in range(checks + 1):
        try:
            stat = path.stat()
            size = (stat.st_size, stat.st_mtime_ns)
            with path.open("rb"):
                pass
        except OSError:
            return False
        if previous is not None and size != previous:
            return False
        previous = size
        if interval and attempt < checks:
            time.sleep(interval)
    return True


@dataclass
class PlanItem:
    source: str
    destination: str
    sha256: str
    action: str
    file_type: str
    capture_date: str | None
    reason: str
    duplicate_of: int | None = None
    status: str = "PLANNED"
    source_size: int | None = None
    source_mtime_ns: int | None = None
    companions: list[dict] = field(default_factory=list)
    live_pair_key: str | None = None
    live_role: str | None = None
    quick_hash: str | None = None
    media_group_id: str | None = None
    source_observed_at: float | None = None


class Organizer:
    def __init__(self, config: Config):
        self.config = config
        self._operation = storage_lock(config.db_path).operation
        self._operation.acquire()
        self._closed = False
        try:
            config.ensure_directories()
            self.db = Database(config.db_path)
        except BaseException:
            self._operation.release()
            raise

    def close(self):
        if not self._closed:
            try: self.db.close()
            finally:
                self._closed = True
                self._operation.release()

    def files(self) -> Iterable[Path]:
        for root, directories, filenames in os.walk(self.config.inbox):
            directories[:] = sorted(name for name in directories if not ignored_path(Path(name)) and not (Path(root) / name).is_symlink())
            for name in sorted(filenames):
                path = Path(root) / name
                if not path.is_symlink() and not ignored_path(path.relative_to(self.config.inbox)):
                    yield path

    @staticmethod
    def _progress(callback, message: str, current: int, total: int, phase: str) -> None:
        if not callback: return
        try: callback(message, current, total, phase)
        except TypeError: callback(message)

    def plan_other(self, source: Path, stamp: str) -> PlanItem:
        observed = time.time()
        before = source.stat()
        digest = sha256_file(source); existing = self.db.find_rejected(digest)
        relative = safe_relative(source.relative_to(self.config.inbox))
        duplicate = bool(existing and existing["destination_path"] and Path(existing["destination_path"]).is_file())
        folder = self.config.rejected_duplicate_dir / stamp if duplicate else self.config.rejected_dir / stamp
        destination = unique_path(folder / relative)
        stat = source.stat()
        if (before.st_size, before.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise IOError(f"檔案仍在寫入，請稍後重新掃描：{source.name}")
        return PlanItem(str(source), str(destination), digest, "OTHER_DUPLICATE" if duplicate else "OTHER",
                        "OTHER", None, "UNSUPPORTED_FILE", source_size=stat.st_size,
                        source_mtime_ns=stat.st_mtime_ns, source_observed_at=observed)

    def plan_file(self, source: Path) -> PlanItem:
        observed = time.time()
        stat = source.stat(); quick = quick_hash_file(source); candidates = self.db.duplicate_candidates(stat.st_size, quick)
        digest = sha256_file(source) if candidates else ""
        existing = next((row for row in candidates if row["sha256"] == digest), None)
        kind = "RAW" if source.suffix.lower() in RAW_EXTENSIONS else ("VIDEO" if source.suffix.lower() in VIDEO_EXTENSIONS else "JPG")
        date, reason = capture_date(source, self.config.filesystem_date_fallback)
        if existing:
            folder = self.config.duplicate_dir / datetime.now().strftime("%Y/%Y-%m-%d")
            action = "DUPLICATE"
        elif date:
            root = self.config.raw_dir if kind == "RAW" else (self.config.video_dir if kind == "VIDEO" else self.config.jpg_dir)
            folder = root / date.strftime("%Y/%Y-%m-%d")
            action = "IMPORT"
        else:
            folder = self.config.unsorted_dir
            action = "UNSORTED"
        destination = unique_path(folder / safe_filename(source.name))
        companions = []
        if kind == "RAW":
            for suffix in (".xmp", ".XMP"):
                sidecar = source.with_suffix(suffix)
                if sidecar.is_file():
                    companions.append({"source": str(sidecar)})
                    break
        if not date and companions:
            xmp_date, xmp_reason = capture_date(Path(companions[0]["source"]), False)
            if xmp_date:
                date, reason = xmp_date, f"XMP:{xmp_reason}"
                if not existing:
                    action = "IMPORT"; destination = unique_path(self.config.raw_dir / date.strftime("%Y/%Y-%m-%d") / safe_filename(source.name))
        return PlanItem(str(source), str(destination), digest, action, kind, date.isoformat() if date else None,
                        reason, int(existing["id"]) if existing else None, source_size=stat.st_size,
                        source_mtime_ns=stat.st_mtime_ns, companions=companions, quick_hash=quick,
                        media_group_id=media_group_identifier(source), source_observed_at=observed)

    def scan(self, progress: Callable | None = None) -> list[PlanItem]:
        self._progress(progress, "建立 Inbox 檔案清單", 0, 0, "listing")
        output = []; all_files = list(self.files()); by_stem = {}; stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        for path in all_files: by_stem.setdefault((path.parent.resolve(), path.stem.casefold()), []).append(path)
        companions = {p.resolve() for paths in by_stem.values() if any(x.suffix.lower() in RAW_EXTENSIONS for x in paths)
                      for p in paths if p.suffix.lower() == ".xmp"}
        files = [path for path in all_files if path.resolve() not in companions]
        seen: dict[tuple[int, str], list[PlanItem]] = {}
        planned = {}
        total = len(files)
        for current, source in enumerate(files, 1):
            self._progress(progress, f"讀取 {current} / {total}：{source.name}", current - 1, total, "reading")
            if source.suffix.lower() in SUPPORTED_EXTENSIONS:
                item = self.plan_file(source)
                key = (item.source_size or 0, item.quick_hash or "")
                matches = seen.get(key, [])
                if matches and item.action != "DUPLICATE":
                    if not item.sha256: item.sha256 = sha256_file(source)
                    prior = next((x for x in matches if (x.sha256 or sha256_file(Path(x.source))) == item.sha256), None)
                    if prior:
                        if not prior.sha256: prior.sha256 = item.sha256
                        item.action = "DUPLICATE"; item.reason = "DUPLICATE_IN_BATCH"
                        item.destination = str(unique_path(self.config.duplicate_dir / datetime.now().strftime("%Y/%Y-%m-%d") / safe_filename(source.name)))
                seen.setdefault(key, []).append(item)
            else:
                item = self.plan_other(source, stamp)
                other_key = (item.source_size or 0, item.sha256)
                if other_key in seen:
                    item.action = "OTHER_DUPLICATE"; item.reason = "DUPLICATE_IN_BATCH"
                    item.destination = str(unique_path(self.config.rejected_duplicate_dir / stamp / safe_relative(source.relative_to(self.config.inbox))))
                seen.setdefault(other_key, []).append(item)
            planned[source] = item
            output.append(item)
        live_images = {".jpg", ".jpeg", ".heic", ".heif"}; identity_groups = {}
        for path, item in planned.items():
            if item.media_group_id: identity_groups.setdefault(item.media_group_id, []).append(path)
        groups = [((Path("."), identity), paths) for identity, paths in identity_groups.items()] + list(by_stem.items())
        paired_sources = set()
        for (_, stem), paths in groups:
            photos = [p for p in paths if p.suffix.lower() in live_images and p not in paired_sources]
            videos = [p for p in paths if p.suffix.lower() == ".mov" and p not in paired_sources]
            if len(photos) != 1 or len(videos) != 1: continue
            photo, video = photos[0], videos[0]
            if not photo or not video or photo in paired_sources or video in paired_sources: continue
            photo_item, video_item = planned[photo], planned[video]
            if (photo_item.media_group_id and video_item.media_group_id and
                    photo_item.media_group_id != video_item.media_group_id): continue
            if "DUPLICATE" in {photo_item.action, video_item.action}: continue
            pair_date = photo_item.capture_date or video_item.capture_date
            key = photo_item.media_group_id or video_item.media_group_id or hashlib.sha256(f"{photo.parent.resolve()}|{stem}".encode()).hexdigest()[:24]
            if pair_date:
                date = datetime.fromisoformat(pair_date); folder = self.config.live_dir / date.strftime("%Y/%Y-%m-%d")
            else:
                folder = self.config.live_dir / "_Unsorted"
            photo_name = safe_filename(photo.name); video_name = Path(photo_name).stem + video.suffix.lower()
            photo_destination, video_destination = unique_live_paths(folder / photo_name, folder / video_name)
            photo_item.destination = str(photo_destination); photo_item.live_pair_key = key; photo_item.live_role = "PHOTO"
            video_item.destination = str(video_destination); video_item.live_pair_key = key; video_item.live_role = "VIDEO"
            photo_item.capture_date = video_item.capture_date = pair_date
            paired_sources.update((photo, video))
        self._progress(progress, f"掃描完成，共 {total} 個檔案", total, total, "preview")
        return output

    def _safe_copy(self, source: Path, destination: Path, expected_hash: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
        try:
            with source.open("rb") as src, temporary.open("xb") as dst:
                shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            shutil.copystat(source, temporary)
            if sha256_file(temporary) != expected_hash:
                raise IOError("複製後 SHA-256 驗證失敗")
            # Publishing a verified copy must never overwrite a racing writer.
            os.link(temporary, destination)
            temporary.unlink()
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

    def _safe_place(self, source: Path, destination: Path, expected_hash: str) -> bool:
        """Publish without replacement, retaining Inbox until the DB commits.

        A same-device hard link avoids copying bytes. Unsupported hard links
        fall back to verified copy; failure always leaves the source intact.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if source.stat().st_dev == destination.parent.stat().st_dev:
                os.link(source, destination)
                return False
        except FileExistsError:
            raise
        except OSError:
            pass
        self._safe_copy(source, destination, expected_hash)
        return False

    @staticmethod
    def _group_destination(destination: Path, companions: list[dict]) -> Path:
        candidate = destination
        for number in range(100_000):
            paths = [candidate] + [candidate.with_suffix(Path(c["source"]).suffix) for c in companions]
            if all(not os.path.lexists(p) for p in paths): return candidate
            candidate = destination.with_name(f"{destination.stem}_{number + 1:03d}{destination.suffix}")
        raise FileExistsError("無法保留不衝突的主檔及附屬檔名稱")

    def _move_file(self, source: Path, destination: Path):
        digest = sha256_file(source)
        self._safe_place(source, destination, digest)
        try: source.unlink()
        except OSError:
            # We published this destination, and the source is still present.
            destination.unlink(missing_ok=True)
            raise

    def execute(self, items: list[PlanItem], progress: Callable | None = None) -> Path:
        batch_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        manifest = self.config.manifests_dir / f"{batch_id}.jsonl"
        total = len(items)
        for current, item in enumerate(items, 1):
            source, destination = Path(item.source), Path(item.destination)
            database_committed = False
            moved_atomically = False
            destination_created = False
            placed_companions: list[tuple[Path, Path, bool, str]] = []
            try:
                if source.is_symlink() or not source.resolve().is_relative_to(self.config.inbox.resolve()):
                    raise ValueError("來源必須位於使用者 Inbox 內")
                if not any(destination.resolve().is_relative_to(root.resolve()) for root in (self.config.library, self.config.rejected_dir)):
                    raise ValueError("目的地必須位於使用者的圖庫或隔離區內")
                if not source.exists():
                    raise FileNotFoundError("來源檔案已不存在")
                destination = self._group_destination(destination, item.companions)
                item.destination = str(destination)
                stat = source.stat()
                unchanged_since_preview = (item.source_size == stat.st_size and item.source_mtime_ns == stat.st_mtime_ns)
                if not unchanged_since_preview:
                    raise IOError("檔案在預覽後已有變更，請重新掃描")
                observation_window = self.config.stable_interval * self.config.stable_checks
                observed_stable = (item.source_observed_at is not None and
                                   time.time() - item.source_observed_at >= observation_window)
                if not observed_stable and not stable(source, self.config.stable_interval, self.config.stable_checks):
                    raise IOError("檔案尚未寫入完成或無法讀取")
                current_digest = sha256_file(source)
                after_hash = source.stat()
                if (stat.st_size, stat.st_mtime_ns) != (after_hash.st_size, after_hash.st_mtime_ns):
                    raise IOError("檔案仍在寫入，請稍後重新掃描")
                if item.sha256 and current_digest != item.sha256:
                    raise IOError("檔案在預覽後已有變更，請重新掃描")
                item.sha256 = current_digest
                if item.file_type != "OTHER" and item.action not in {"DUPLICATE"}:
                    existing = self.db.find_photo(item.sha256)
                    if existing:
                        item.action = "DUPLICATE"; item.duplicate_of = int(existing["id"])
                        destination = unique_path(self.config.duplicate_dir / datetime.now().strftime("%Y/%Y-%m-%d") / safe_filename(source.name))
                        item.destination = str(destination)
                destination = self._group_destination(destination, item.companions)
                item.destination = str(destination)
                self._progress(progress, f"處理 {current} / {total}：{source.name}", current - 1, total, "moving")
                # The source stays in Inbox until the verified destination and
                # database record both exist. A crash can therefore duplicate a
                # file, but cannot lose the only copy.
                moved_atomically = self._safe_place(source, destination, item.sha256)
                destination_created = True
                for companion in item.companions:
                    companion_source = Path(companion["source"])
                    companion_hash = sha256_file(companion_source)
                    companion_destination = destination.with_suffix(companion_source.suffix)
                    companion_moved = self._safe_place(companion_source, companion_destination, companion_hash)
                    companion.update(destination=str(companion_destination), sha256=companion_hash, status="COMPLETED")
                    placed_companions.append((companion_source, companion_destination, companion_moved, companion_hash))
                item.status = "COMPLETED"
                metadata = ({**index_metadata(destination), "metadata_indexed_at": now_iso()}
                            if item.action not in {"DUPLICATE", "OTHER", "OTHER_DUPLICATE"} else None)
                if (source.stat().st_size, source.stat().st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                    raise IOError("檔案在整理時變更，已保留來源，請重新掃描")
                with self.db.transaction():
                    if item.action not in {"DUPLICATE", "OTHER", "OTHER_DUPLICATE"}:
                        photo_id = self.db.add_photo(
                            sha256=item.sha256, original_path=item.source, current_path=item.destination,
                            capture_date=item.capture_date, file_type=item.file_type,
                            extension=source.suffix.lower(), file_size=destination.stat().st_size,
                            imported_at=now_iso(), status="ACTIVE",
                        )
                        if item.live_pair_key:
                            self.db.connection.execute(
                                "UPDATE photos SET live_pair_key=?,live_role=? WHERE id=?",
                                (item.live_pair_key, item.live_role, photo_id),
                            )
                        self.db.connection.execute("UPDATE photos SET quick_hash=? WHERE id=?", (item.quick_hash, photo_id))
                        self.db.update_photo_metadata(photo_id, metadata)
                    self.db.add_import(
                        batch_id=batch_id, source_path=item.source, destination_path=item.destination,
                        sha256=item.sha256, action=item.action, status=item.status,
                        duplicate_of=item.duplicate_of, error=None, created_at=now_iso(),
                    )
                database_committed = True
                try:
                    if not moved_atomically:
                        source.unlink()
                    for companion_source, _, companion_moved, _ in placed_companions:
                        if not companion_moved:
                            companion_source.unlink()
                except OSError:
                    # The organized copy and index are valid. Keeping an extra
                    # Inbox copy is safer than treating the import as lost.
                    item.status = "SOURCE_RETAINED"
            except InterruptedError:
                item.status = "PLANNED"
                raise
            except Exception as exc:
                item.status = "FAILED"
                # A database failure must not leave an untracked library file.
                # Keep the original Inbox file and remove only the verified copy
                # created by this operation.
                if not database_committed and moved_atomically and destination.exists() and not source.exists():
                    try:
                        destination.replace(source)
                    except OSError:
                        pass
                elif not database_committed and destination_created and source.exists() and destination.exists():
                    try:
                        if sha256_file(destination) == item.sha256:
                            destination.unlink()
                    except OSError:
                        pass
                if not database_committed:
                    for companion_source, companion_destination, companion_moved, companion_hash in reversed(placed_companions):
                        try:
                            if companion_moved and companion_destination.exists() and not companion_source.exists():
                                companion_destination.replace(companion_source)
                            elif companion_source.exists() and companion_destination.exists() and sha256_file(companion_destination) == companion_hash:
                                companion_destination.unlink()
                        except OSError:
                            pass
                with self.db.transaction():
                    self.db.add_import(
                        batch_id=batch_id, source_path=item.source, destination_path=item.destination,
                        sha256=item.sha256, action=item.action, status=item.status,
                        duplicate_of=item.duplicate_of, error=str(exc), created_at=now_iso(),
                    )
            with manifest.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"batch_id": batch_id, "timestamp": now_iso(), **asdict(item)}, ensure_ascii=False) + "\n")
                handle.flush(); os.fsync(handle.fileno())
        self.pair_live_photos()
        self.cleanup_empty_inbox_directories()
        self._progress(progress, "整理完成", total, total, "complete")
        return manifest

    def cleanup_empty_inbox_directories(self) -> int:
        removed = 0
        directories = sorted((p for p in self.config.inbox.rglob("*") if p.is_dir()),
                             key=lambda p: len(p.parts), reverse=True)
        for directory in directories:
            try:
                directory.rmdir(); removed += 1
            except OSError:
                pass
        return removed

    def reindex(self, progress: Callable[[str], None] | None = None) -> tuple[int, int]:
        added = skipped = 0
        roots = (self.config.raw_dir, self.config.jpg_dir, self.config.video_dir, self.config.live_dir, self.config.unsorted_dir)
        for root in roots:
            for path in root.rglob("*"):
                if path.is_symlink() or ignored_path(path.relative_to(root)) or not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                if progress:
                    progress(f"建立索引 {path.name}")
                digest = sha256_file(path)
                quick = quick_hash_file(path)
                existing = self.db.find_photo(digest)
                metadata = ({**index_metadata(path), "metadata_indexed_at": now_iso()}
                            if not existing or not existing["metadata_indexed_at"] else None)
                if existing:
                    # A copied library keeps the same hashes but gets new paths.
                    # Reindex repairs those paths without moving any photo.
                    with self.db.transaction():
                        self.db.connection.execute(
                            "UPDATE photos SET current_path=?, file_size=?, quick_hash=?, status='ACTIVE' WHERE id=?",
                            (str(path), path.stat().st_size, quick, existing["id"]),
                        )
                        if not existing["metadata_indexed_at"]:
                            self.db.update_photo_metadata(existing["id"], metadata)
                    skipped += 1
                    continue
                date, _ = capture_date(path, self.config.filesystem_date_fallback)
                kind = "RAW" if path.suffix.lower() in RAW_EXTENSIONS else ("VIDEO" if path.suffix.lower() in VIDEO_EXTENSIONS else "JPG")
                with self.db.transaction():
                    photo_id = self.db.add_photo(sha256=digest, original_path=str(path), current_path=str(path),
                                      capture_date=date.isoformat() if date else None, file_type=kind,
                                      extension=path.suffix.lower(), file_size=path.stat().st_size,
                                      imported_at=now_iso(), status="ACTIVE")
                    self.db.update_photo_metadata(photo_id, metadata)
                    self.db.connection.execute("UPDATE photos SET quick_hash=? WHERE id=?", (quick, photo_id))
                added += 1
        self.pair_live_photos()
        return added, skipped

    def reorganize_unsorted(self, progress: Callable[[str], None] | None = None) -> tuple[int, int]:
        """Move only the staging `_Unsorted` area after EXIF becomes available.

        Established RAW/JPG library folders are intentionally never touched.
        """
        moved = unchanged = 0
        batch_id = datetime.now().strftime("%Y%m%d-%H%M%S-unsorted-") + uuid.uuid4().hex[:8]
        manifest = self.config.manifests_dir / f"{batch_id}.jsonl"
        for source in sorted(self.config.unsorted_dir.rglob("*")):
            if not source.is_file() or source.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if progress:
                progress(f"重新讀取 {source.name}")
            date, reason = capture_date(source, False)
            if not date:
                unchanged += 1
                continue
            kind = "RAW" if source.suffix.lower() in RAW_EXTENSIONS else ("VIDEO" if source.suffix.lower() in VIDEO_EXTENSIONS else "JPG")
            root = self.config.raw_dir if kind == "RAW" else (self.config.video_dir if kind == "VIDEO" else self.config.jpg_dir)
            destination = unique_path(root / date.strftime("%Y/%Y-%m-%d") / source.name)
            digest = sha256_file(source)
            record = PlanItem(str(source), str(destination), digest, "REORGANIZE_UNSORTED",
                              kind, date.isoformat(), reason)
            try:
                self._safe_copy(source, destination, digest)
                with self.db.transaction():
                    row = self.db.find_photo(digest)
                    if row:
                        self.db.connection.execute(
                            "UPDATE photos SET current_path=?, capture_date=?, file_type=?, status='ACTIVE' WHERE id=?",
                            (str(destination), date.isoformat(), kind, row["id"]),
                        )
                    else:
                        self.db.add_photo(sha256=digest, original_path=str(source), current_path=str(destination),
                                          capture_date=date.isoformat(), file_type=kind,
                                          extension=source.suffix.lower(), file_size=destination.stat().st_size,
                                          imported_at=now_iso(), status="ACTIVE")
                    self.db.add_import(batch_id=batch_id, source_path=str(source), destination_path=str(destination),
                                       sha256=digest, action=record.action, status="COMPLETED",
                                       duplicate_of=None, error=None, created_at=now_iso())
                source.unlink()
                record.status = "COMPLETED"
                moved += 1
            except Exception as exc:
                record.status = "FAILED"
                record.reason = str(exc)
                if source.exists() and destination.exists() and sha256_file(destination) == digest:
                    destination.unlink()
                unchanged += 1
            with manifest.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"batch_id": batch_id, "timestamp": now_iso(), **asdict(record)}, ensure_ascii=False) + "\n")
        return moved, unchanged

    def undo(self, manifest: Path | None = None) -> tuple[int, int]:
        manifests = sorted(self.config.manifests_dir.glob("*.jsonl"))
        if manifest is None:
            candidates = [p for p in manifests if not p.name.endswith(".undone.jsonl")]
            if not candidates:
                return 0, 0
            manifest = candidates[-1]
        records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line]
        restored = failed = 0
        for record in reversed(records):
            if record["status"] not in {"COMPLETED", "SOURCE_RETAINED"}:
                continue
            source, destination = Path(record["source"]), Path(record["destination"])
            try:
                if not destination.exists() or sha256_file(destination) != record["sha256"]:
                    raise IOError("目的檔不存在或內容已改變")
                target = unique_path(source)
                self._safe_copy(destination, target, record["sha256"])
                with self.db.transaction():
                    self.db.connection.execute("DELETE FROM imports WHERE batch_id=? AND destination_path=?", (record["batch_id"], record["destination"]))
                    if record["action"] == "REORGANIZE_UNSORTED":
                        self.db.connection.execute(
                            "UPDATE photos SET current_path=?, capture_date=NULL WHERE sha256=? AND current_path=?",
                            (str(target), record["sha256"], record["destination"]),
                        )
                    elif record["action"] != "DUPLICATE":
                        self.db.connection.execute("DELETE FROM photos WHERE sha256=? AND current_path=?", (record["sha256"], record["destination"]))
                destination.unlink()
                for companion in record.get("companions", []):
                    companion_destination = Path(companion.get("destination", ""))
                    companion_source = Path(companion.get("source", ""))
                    if companion_destination.is_file() and companion.get("sha256") == sha256_file(companion_destination):
                        companion_target = unique_path(companion_source)
                        self._safe_copy(companion_destination, companion_target, companion["sha256"])
                        companion_destination.unlink()
                restored += 1
            except Exception:
                failed += 1
        manifest.rename(manifest.with_name(manifest.stem + ".undone.jsonl"))
        return restored, failed

    def recover(self) -> int:
        removed = 0
        for partial in self.config.library.rglob(".*.partial"):
            if partial.is_file():
                partial.unlink()
                removed += 1
        return removed

    def backup_index(self, keep: int = 10) -> Path:
        """Create a consistent SQLite snapshot; never copy a live WAL database."""
        self.config.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = unique_path(self.config.backups_dir / f"photo-organizer-{stamp}.db")
        temporary = destination.with_suffix(".db.partial")
        target = sqlite3.connect(temporary)
        try:
            self.db.connection.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("索引備份完整性檢查失敗")
            target.close()
            temporary.replace(destination)
        except Exception:
            target.close()
            if temporary.exists():
                temporary.unlink()
            raise
        backups = sorted(self.config.backups_dir.glob("photo-organizer-*.db"))
        for old in backups[:-max(1, keep)]:
            old.unlink()
        return destination

    def move_to_trash(self, photo_ids: list[int]) -> tuple[int, int]:
        moved = failed = 0
        stamp = datetime.now().strftime("%Y-%m-%d")
        for row in self.db.photos_by_ids(photo_ids):
            source = Path(row["current_path"])
            try:
                if not source.is_file(): raise FileNotFoundError(source)
                destination = unique_path(self.config.trash_dir / stamp / source.name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._move_file(source, destination)
                try:
                    with self.db.transaction():
                        self.db.connection.execute(
                            "UPDATE photos SET current_path=?,trash_original_path=?,status='TRASH',deleted_at=? WHERE id=?",
                            (str(destination), str(source), now_iso(), row["id"]),
                        )
                except Exception:
                    self._move_file(destination, source); raise
                moved += 1
            except Exception:
                failed += 1
        return moved, failed

    def restore_from_trash(self, photo_ids: list[int]) -> tuple[int, int]:
        restored = failed = 0
        photo_ids = self.db.linked_ids(photo_ids)
        placeholders = ",".join("?" for _ in photo_ids) or "NULL"
        rows = self.db.connection.execute(
            f"SELECT * FROM photos WHERE status='TRASH' AND id IN ({placeholders})", photo_ids
        ).fetchall()
        for row in rows:
            source = Path(row["current_path"])
            try:
                if not source.is_file(): raise FileNotFoundError(source)
                destination = unique_path(Path(row["trash_original_path"]))
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._move_file(source, destination)
                try:
                    with self.db.transaction():
                        self.db.connection.execute(
                            "UPDATE photos SET current_path=?,trash_original_path=NULL,status='ACTIVE',deleted_at=NULL WHERE id=?",
                            (str(destination), row["id"]),
                        )
                except Exception:
                    self._move_file(destination, source); raise
                restored += 1
            except Exception:
                failed += 1
        return restored, failed

    def pair_live_photos(self) -> int:
        rows = self.db.connection.execute(
            "SELECT id,current_path,capture_date,extension,live_partner_id,live_pair_key,content_identifier FROM photos WHERE status='ACTIVE'"
        ).fetchall(); groups = {}
        for row in rows:
            suffix = row["extension"].lower()
            if suffix not in {".jpg", ".jpeg", ".heic", ".heif", ".mov"}: continue
            path = Path(row["current_path"]); folder_day = path.parent.name
            day = folder_day if len(folder_day) == 10 and folder_day[4:5] == "-" and folder_day[7:8] == "-" else (row["capture_date"] or "")[:10]
            group_key = (f"pair:{row['live_pair_key']}" if row["live_pair_key"] else
                         f"identity:{row['content_identifier']}" if row["content_identifier"] else
                         f"stem:{path.stem.casefold()}|{day}")
            groups.setdefault(group_key, {"photos": [], "videos": []})[
                "videos" if suffix == ".mov" else "photos"
            ].append(row)
        paired = 0
        with self.db.transaction():
            for group in groups.values():
                photos = sorted(group["photos"], key=lambda r: r["id"]); videos = sorted(group["videos"], key=lambda r: r["id"])
                if len(photos) != 1 or len(videos) != 1: continue
                for photo, video in zip(photos, videos):
                    if photo["live_partner_id"] or video["live_partner_id"]: continue
                    key = uuid.uuid4().hex
                    self.db.connection.execute(
                        "UPDATE photos SET live_pair_key=?,live_role='PHOTO',live_partner_id=? WHERE id=?",
                        (key, video["id"], photo["id"]),
                    )
                    self.db.connection.execute(
                        "UPDATE photos SET live_pair_key=?,live_role='VIDEO',live_partner_id=? WHERE id=?",
                        (key, photo["id"], video["id"]),
                    ); paired += 1
        return paired

    def live_migration_plan(self) -> list[dict]:
        rows = self.db.connection.execute(
            """SELECT p.id,p.current_path,p.capture_date,p.live_partner_id,v.current_path AS video_path
               FROM photos p JOIN photos v ON v.id=p.live_partner_id
               WHERE p.status='ACTIVE' AND v.status='ACTIVE' AND p.live_role='PHOTO'"""
        ).fetchall(); output = []
        live_root = self.config.live_dir.resolve()
        for row in rows:
            photo, video = Path(row["current_path"]), Path(row["video_path"])
            try:
                if photo.resolve().is_relative_to(live_root) and video.resolve().is_relative_to(live_root): continue
            except OSError: pass
            date = datetime.fromisoformat(row["capture_date"]) if row["capture_date"] else None
            folder = self.config.live_dir / (date.strftime("%Y/%Y-%m-%d") if date else "_Unsorted")
            output.append({"photo_id": row["id"], "video_id": row["live_partner_id"],
                           "photo_source": str(photo), "video_source": str(video),
                           "photo_destination": str(folder / photo.name), "video_destination": str(folder / video.name)})
        return output

    def migrate_live_photos(self, progress: Callable[[str], None] | None = None) -> dict:
        plan = self.live_migration_plan(); completed = failed = 0
        self.backup_index()
        for item in plan:
            photo, video = Path(item["photo_source"]), Path(item["video_source"])
            photo_dest, video_dest = unique_live_paths(Path(item["photo_destination"]), Path(item["video_destination"]))
            moved_photo = False
            try:
                if progress: progress(f"整理 Live Photo {photo.stem}")
                if not photo.is_file() or not video.is_file(): raise FileNotFoundError("Live Photo 配對檔案不存在")
                photo_dest.parent.mkdir(parents=True, exist_ok=True)
                self._move_file(photo, photo_dest); moved_photo = True; self._move_file(video, video_dest)
                try:
                    with self.db.transaction():
                        self.db.connection.execute("UPDATE photos SET current_path=? WHERE id=?", (str(photo_dest), item["photo_id"]))
                        self.db.connection.execute("UPDATE photos SET current_path=? WHERE id=?", (str(video_dest), item["video_id"]))
                except Exception:
                    self._move_file(video_dest, video); self._move_file(photo_dest, photo); raise
                completed += 1
            except Exception:
                if moved_photo and photo_dest.exists() and not photo.exists():
                    try: self._move_file(photo_dest, photo)
                    except OSError: pass
                failed += 1
        return {"planned": len(plan), "completed": completed, "failed": failed}

    def health_check(self) -> dict:
        missing = []
        indexed_paths = set()
        for row in self.db.connection.execute("SELECT id,current_path,status FROM photos"):
            path = Path(row["current_path"]); indexed_paths.add(str(path.resolve()))
            if not path.is_file(): missing.append({"id": row["id"], "path": str(path), "status": row["status"]})
        unindexed = []
        for root in (self.config.raw_dir, self.config.jpg_dir, self.config.video_dir, self.config.live_dir, self.config.unsorted_dir):
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS and str(path.resolve()) not in indexed_paths:
                    unindexed.append(str(path))
        integrity = self.db.connection.execute("PRAGMA integrity_check").fetchone()[0]
        return {"database": integrity, "missing": missing[:500], "unindexed": unindexed[:500],
                "missing_count": len(missing), "unindexed_count": len(unindexed)}

    def repair_health(self, progress: Callable[[str], None] | None = None) -> dict:
        report = self.health_check(); missing_ids = [item["id"] for item in report["missing"]]
        if missing_ids:
            placeholders = ",".join("?" for _ in missing_ids)
            with self.db.transaction():
                self.db.connection.execute(f"UPDATE photos SET status='MISSING' WHERE id IN ({placeholders})", missing_ids)
        added, existing = self.reindex(progress)
        return {"marked_missing": len(missing_ids), "added": added, "repaired_existing": existing,
                "after": self.health_check()}

    def purge_trash(self, days: int) -> tuple[int, int]:
        cutoff = datetime.now(timezone.utc).timestamp() - max(1, days) * 86400
        purged = failed = 0
        rows = self.db.connection.execute("SELECT * FROM photos WHERE status='TRASH'").fetchall()
        for row in rows:
            try:
                deleted = datetime.fromisoformat(row["deleted_at"]).timestamp() if row["deleted_at"] else time.time()
                if deleted > cutoff: continue
                path = Path(row["current_path"])
                with self.db.transaction():
                    self.db.connection.execute("UPDATE imports SET duplicate_of=NULL WHERE duplicate_of=?", (row["id"],))
                    self.db.connection.execute("UPDATE photos SET live_partner_id=NULL,live_role=NULL,live_pair_key=NULL WHERE live_partner_id=?", (row["id"],))
                    self.db.connection.execute("DELETE FROM photos WHERE id=?", (row["id"],))
                    if path.is_file(): path.unlink()
                purged += 1
            except Exception:
                failed += 1
        return purged, failed


def restore_index_backup(config: Config, source: Path) -> Path:
    with storage_lock(config.db_path).exclusive():
        return _restore_index_backup(config, source)


def _restore_index_backup(config: Config, source: Path) -> Path:
    """Restore a verified snapshot after first preserving the current index."""
    source = source.resolve(); backups = config.backups_dir.resolve()
    if not source.is_file() or source.parent != backups:
        raise FileNotFoundError("找不到備份")
    check = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("備份檔完整性檢查失敗")
    finally:
        check.close()
    organizer = Organizer(config)
    try: safety = organizer.backup_index(keep=100_000)
    finally: organizer.close()
    temporary = config.db_path.with_suffix(".db.restore-partial"); temporary.unlink(missing_ok=True)
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True); target = sqlite3.connect(temporary)
    try:
        source_db.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise ValueError("還原後完整性檢查失敗")
    finally:
        target.close(); source_db.close()
    for suffix in ("-wal", "-shm"): Path(str(config.db_path) + suffix).unlink(missing_ok=True)
    temporary.replace(config.db_path)
    verified = Database(config.db_path)
    try:
        if verified.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise ValueError("還原後資料庫無法使用")
    finally: verified.close()
    return safety

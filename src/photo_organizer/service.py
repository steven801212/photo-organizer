from __future__ import annotations

from dataclasses import asdict
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import parse_qs, urlparse
import hashlib, json, mimetypes, os, tempfile, time, traceback, urllib.request, zipfile

from .auth import AuthStore
from .config import Config
from .engine import Organizer, PlanItem, now_iso, restore_index_backup
from .metadata import exif_details, exiftool_version
from .database import Database
from .thumbnails import thumbnail
from . import __version__

WEB = Path(__file__).resolve().parent / "web"
SYSTEM_DATA = Path(os.environ.get("PHOTO_DATA", "/data"))
AUTH: AuthStore | None = None


def tenant_config(username: str) -> Config:
    return Config(
        inbox=Path(os.environ.get("PHOTO_INBOX_ROOT", "/inbox")) / username,
        library=Path(os.environ.get("PHOTO_LIBRARY_ROOT", "/library")) / username,
        index_dir=SYSTEM_DATA / "users" / username,
        backup_dir=Path(os.environ.get("PHOTO_BACKUP_ROOT", "/backup")) / username,
        reject_dir=Path(os.environ.get("PHOTO_REJECT_ROOT", str(SYSTEM_DATA.parent / "rejected"))) / username,
        stable_interval=float(os.environ.get("STABLE_INTERVAL", "2")),
        stable_checks=int(os.environ.get("STABLE_CHECKS", "2")),
    )


class State:
    def __init__(self, config: Config):
        self.state_file = config.system_dir / "job-state.json"; self.plan_file = config.system_dir / "scan-plan.json"
        self.lock = Lock(); self.busy = False; self.phase = "idle"; self.message = "等待操作"
        self.progress = 0; self.processed = 0; self.work_total = 0; self.items: list[PlanItem] = []; self.last_result = {}; self.error = None
        self.cancel_requested = False; self.last_persist = 0.0
        try:
            saved = json.loads(self.state_file.read_text(encoding="utf-8"))
            for key in ("phase", "message", "progress", "processed", "work_total", "last_result", "error"):
                if key in saved: setattr(self, key, saved[key])
            if saved.get("busy"):
                self.phase = "interrupted"; self.message = "上一個工作因服務重啟中斷，請重新掃描或檢查 Inbox"
            if self.plan_file.is_file():
                self.items = [PlanItem(**item) for item in json.loads(self.plan_file.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            self.items = []

    def snapshot(self, offset: int = 0, limit: int = 500):
        with self.lock:
            offset = max(0, offset); limit = max(1, min(500, limit))
            counts = {}
            for item in self.items: counts[item.action] = counts.get(item.action, 0) + 1
            return {"busy": self.busy, "phase": self.phase, "message": self.message, "progress": self.progress,
                    "processed": self.processed, "work_total": self.work_total,
                    "counts": counts, "total": len(self.items), "total_bytes": sum(x.source_size or 0 for x in self.items), "error": self.error,
                    "preview_offset": offset, "preview_limit": limit,
                    "last_result": self.last_result, "items": [asdict(x) for x in self.items[offset:offset + limit]]}

    def planned_items(self) -> list[PlanItem]:
        with self.lock: return [PlanItem(**asdict(item)) for item in self.items]

    def item_at(self, index: int) -> PlanItem | None:
        with self.lock: return self.items[index] if 0 <= index < len(self.items) else None

    def item_count(self) -> int:
        with self.lock: return len(self.items)

    def update(self, **values):
        with self.lock:
            for key, value in values.items(): setattr(self, key, value)
            try:
                if "items" in values:
                    temporary = self.plan_file.with_suffix(".json.partial")
                    temporary.write_text(json.dumps([asdict(x) for x in self.items], ensure_ascii=False), encoding="utf-8")
                    temporary.replace(self.plan_file)
                now = time.monotonic()
                if now - self.last_persist >= 0.5 or not self.busy or self.progress >= 100:
                    payload = {key: getattr(self, key) for key in
                               ("busy", "phase", "message", "progress", "processed", "work_total", "last_result", "error")}
                    temporary = self.state_file.with_suffix(".json.partial")
                    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    temporary.replace(self.state_file); self.last_persist = now
            except OSError:
                pass


STATES: dict[str, State] = {}; STATES_LOCK = Lock()


def user_state(username):
    with STATES_LOCK:
        if username not in STATES:
            STATES[username] = State(tenant_config(username))
        return STATES[username]


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists(): return 0
    for item in path.rglob("*"):
        try:
            if item.is_file(): total += item.stat().st_size
        except OSError:
            continue
    return total


def send_notification(username, message, result=None):
    db = Database(tenant_config(username).db_path)
    try: url = db.settings().get("notification_url", "").strip()
    finally: db.close()
    if not url.startswith(("http://", "https://")): return
    payload = json.dumps({"app": "Photo Organizer", "user": username, "message": message,
                          "result": result or {}}, ensure_ascii=False).encode()
    try: urllib.request.urlopen(urllib.request.Request(url, payload, {"Content-Type": "application/json"}), timeout=10).close()
    except Exception: pass


def run_job(state, name, work, username=None):
    with state.lock:
        if state.busy: return False
        state.busy = True; state.phase = name; state.message = "準備中"; state.progress = 0; state.processed = 0; state.work_total = 0; state.error = None; state.cancel_requested = False
    state.update()
    def runner():
        try:
            result = work() or {}
            summary = result.get("summary")
            message = (f"整理完成：成功 {summary['completed']}，失敗 {summary['failed']}"
                       if summary else "完成")
            state.update(last_result=result, message=message, progress=100)
            if username: send_notification(username, message, result)
        except InterruptedError:
            state.update(message="已停止；未處理的檔案仍留在 Inbox", error=None)
        except Exception as exc:
            traceback.print_exc(); state.update(error=str(exc), message="操作失敗")
            if username: send_notification(username, f"{name} 操作失敗", {"error": str(exc)})
        finally: state.update(busy=False)
    Thread(target=runner, daemon=True).start(); return True


def scan_user(username, state):
    organizer = Organizer(tenant_config(username))
    def progress(message, current=0, total=0, phase="scan"):
        if state.cancel_requested: raise InterruptedError("使用者已停止操作")
        state.update(message=message, processed=current, work_total=total,
                     progress=round(current * 100 / total) if total else 0, phase=phase)
    try: items = organizer.scan(progress)
    finally: organizer.close()
    state.update(items=items); return {"scanned": len(items)}


def import_user(username, state):
    items = state.planned_items()
    if not items: raise ValueError("請先掃描預覽")
    organizer = Organizer(tenant_config(username))
    try:
        before = organizer.backup_index()
        def progress(message, current=0, total=0, phase="moving"):
            if state.cancel_requested: raise InterruptedError("使用者已停止操作")
            state.update(message=message, processed=current, work_total=total,
                         progress=round(current * 100 / total) if total else 0, phase=phase)
        manifest = organizer.execute(items, progress)
        backup = organizer.backup_index()
    finally: organizer.close()
    completed = sum(item.status in {"COMPLETED", "SOURCE_RETAINED"} for item in items)
    failed = sum(item.status == "FAILED" for item in items)
    state.update(items=[])
    return {"manifest": str(manifest), "backup_before": str(before), "backup": str(backup),
            "summary": {"completed": completed, "failed": failed}}


def maintenance(username, state, kind):
    organizer = Organizer(tenant_config(username))
    def progress(message):
        if state.cancel_requested: raise InterruptedError("使用者已停止操作")
        state.update(message=message)
    try:
        if kind == "reindex":
            added, existing = organizer.reindex(progress); return {"added": added, "existing": existing}
        if kind == "backup": return {"backup": str(organizer.backup_index())}
        if kind == "repair": return organizer.repair_health(progress)
        if kind == "migrate-live": return organizer.migrate_live_photos(progress)
        if kind == "recover": return {"removed": organizer.recover()}
        raise ValueError("未知操作")
    finally: organizer.close()


def scheduler_loop():
    while True:
        time.sleep(60)
        try:
            for account in AUTH.list_users() if AUTH else []:
                if not account["active"]: continue
                username = account["username"]; config = tenant_config(username); db = Database(config.db_path)
                try: settings = db.settings()
                finally: db.close()
                if settings.get("auto_purge", "false").lower() == "true" and settings.get("trash_days"):
                    last_purge = float(settings.get("last_trash_purge_epoch", "0"))
                    if time.time() - last_purge >= 86400:
                        organizer = Organizer(config)
                        try: organizer.purge_trash(int(settings["trash_days"]))
                        finally: organizer.close()
                        db = Database(config.db_path)
                        try: db.set_setting("last_trash_purge_epoch", str(time.time()), now_iso())
                        finally: db.close()
                if settings.get("auto_scan", "false").lower() != "true": continue
                minutes = max(5, int(settings.get("schedule_minutes", "60")))
                last = float(settings.get("last_schedule_epoch", "0"))
                if time.time() - last < minutes * 60: continue
                db = Database(config.db_path)
                try: db.set_setting("last_schedule_epoch", str(time.time()), now_iso())
                finally: db.close()
                state = user_state(username)
                def scheduled_work(username=username, state=state, settings=settings):
                    result = scan_user(username, state)
                    if settings.get("auto_import", "false").lower() == "true" and state.item_count():
                        result = import_user(username, state)
                    return result
                run_job(state, "scheduled", scheduled_work, username)
        except Exception:
            traceback.print_exc()


class Handler(BaseHTTPRequestHandler):
    def json(self, value, status=200, cookie=None):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Vary", "Cookie")
        if cookie: self.send_header("Set-Cookie", cookie)
        self.end_headers(); self.wfile.write(data)

    def body(self):
        return json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 64000)) or b"{}")

    def binary(self, path: Path, content_type: str):
        body = path.read_bytes(); self.send_response(200); self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "private, no-store"); self.send_header("Vary", "Cookie")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def download(self, path: Path, filename: str):
        self.send_response(200); self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(path.stat().st_size)); self.end_headers()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024): self.wfile.write(chunk)

    def media(self, path: Path):
        size = path.stat().st_size; start, end = 0, size - 1
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            try:
                first, last = range_header[6:].split("-", 1); start = int(first or 0); end = min(int(last) if last else end, end)
            except ValueError: return self.json({"error": "Invalid range"}, 416)
        length = max(0, end - start + 1); self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", "video/quicktime"); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, no-store"); self.send_header("Vary", "Cookie")
        if range_header: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length)); self.end_headers()
        with path.open("rb") as source:
            source.seek(start); remaining = length
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk: break
                self.wfile.write(chunk); remaining -= len(chunk)

    def token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", "")); return cookie["photo_session"].value if "photo_session" in cookie else None

    def account(self): return AUTH.session(self.token()) if AUTH else None

    def authenticated(self, csrf=False):
        account = self.account()
        if not account: self.json({"error": "請先登入"}, 401); return None
        if csrf and self.headers.get("X-CSRF-Token") != account["csrf_token"]:
            self.json({"error": "安全驗證失敗，請重新登入"}, 403); return None
        return account

    @staticmethod
    def library_source(config, value):
        source = Path(value).resolve()
        if not source.is_relative_to(config.library.resolve()):
            raise PermissionError("檔案不在目前使用者的圖庫內")
        return source

    def do_GET(self):
        try: self._get()
        except PermissionError: self.json({"error": "無權存取這個檔案"}, 403)
        except (ValueError, TypeError): self.json({"error": "無效的請求參數"}, 400)

    def _get(self):
        parsed = urlparse(self.path); path = parsed.path
        if path == "/api/status":
            account = self.authenticated()
            if not account: return
            query = parse_qs(parsed.query); offset = max(0, int(query.get("offset", ["0"])[0]))
            username = account["username"]; config = tenant_config(username); snap = user_state(username).snapshot(offset, 500)
            snap["user"] = {"username": username, "role": account["role"], "csrf": account["csrf_token"]}
            snap["system"] = {"inboxes": [str(config.inbox)], "library": str(config.library), "data": str(config.system_dir),
                              "backup": str(config.backups_dir), "rejected": str(config.rejected_dir), "exiftool": exiftool_version()}
            snap["system"]["version"] = __version__
            same_device = config.same_storage_device()
            snap["system"]["storage_mode"] = ("atomic" if same_device else "verified-copy") if same_device is not None else "unknown"
            snap["system"]["uid"] = os.getuid() if hasattr(os, "getuid") else None
            snap["system"]["gid"] = os.getgid() if hasattr(os, "getgid") else None
            return self.json(snap)
        if path == "/api/library":
            account = self.authenticated()
            if not account: return
            query = parse_qs(parsed.query); offset = max(0, int(query.get("offset", ["0"])[0])); limit = min(100, max(1, int(query.get("limit", ["60"])[0])))
            config = tenant_config(account["username"]); db = Database(config.db_path)
            try: total, rows = db.library_page(offset, limit, query.get("type", [None])[0],
                                               query.get("year", [None])[0], query.get("day", [None])[0],
                                               query.get("q", [None])[0], query.get("favorites", ["0"])[0] == "1")
            finally: db.close()
            return self.json({"total": total, "offset": offset, "items": [dict(row) for row in rows]})
        if path == "/api/library-groups":
            account = self.authenticated()
            if not account: return
            config = tenant_config(account["username"]); db = Database(config.db_path)
            try: rows = db.library_groups()
            finally: db.close()
            return self.json({"items": [dict(row) for row in rows]})
        if path == "/api/history":
            account = self.authenticated()
            if not account: return
            config = tenant_config(account["username"]); db = Database(config.db_path)
            try: rows = db.recent_imports()
            finally: db.close()
            return self.json({"items": [dict(row) for row in rows]})
        if path == "/api/trash":
            account = self.authenticated()
            if not account: return
            db = Database(tenant_config(account["username"]).db_path)
            offset = max(0, int(parse_qs(parsed.query).get("offset", ["0"])[0]))
            try:
                rows = db.trash_page(offset=offset)
                total = db.connection.execute("SELECT COUNT(*) FROM photos WHERE status='TRASH'").fetchone()[0]
            finally: db.close()
            return self.json({"items": [dict(row) for row in rows], "total": total, "offset": offset})
        if path == "/api/albums":
            account = self.authenticated()
            if not account: return
            db = Database(tenant_config(account["username"]).db_path)
            try: rows = db.albums()
            finally: db.close()
            return self.json({"items": [dict(row) for row in rows]})
        if path.startswith("/api/album/"):
            account = self.authenticated()
            if not account: return
            try: album_id = int(path.rsplit("/", 1)[1])
            except ValueError: return self.json({"error": "Invalid album"}, 400)
            query = parse_qs(parsed.query); offset = max(0, int(query.get("offset", ["0"])[0]))
            db = Database(tenant_config(account["username"]).db_path)
            try: total, rows = db.album_photos(album_id, offset)
            finally: db.close()
            return self.json({"total": total, "items": [dict(row) for row in rows]})
        if path == "/api/backups":
            account = self.authenticated()
            if not account: return
            config = tenant_config(account["username"])
            items = [{"name": p.name, "size": p.stat().st_size, "modified": p.stat().st_mtime}
                     for p in sorted(config.backups_dir.glob("photo-organizer-*.db"), reverse=True)]
            return self.json({"items": items})
        if path == "/api/settings":
            account = self.authenticated()
            if not account: return
            db = Database(tenant_config(account["username"]).db_path)
            try: values = db.settings()
            finally: db.close()
            return self.json({"settings": values})
        if path == "/api/map":
            account = self.authenticated()
            if not account: return
            db = Database(tenant_config(account["username"]).db_path)
            offset = max(0, int(parse_qs(parsed.query).get("offset", ["0"])[0]))
            try:
                rows = db.map_points(offset=offset)
                total = db.map_count()
            finally: db.close()
            return self.json({"items": [dict(row) for row in rows], "total": total, "offset": offset})
        if path == "/api/health-check":
            account = self.authenticated()
            if not account: return
            organizer = Organizer(tenant_config(account["username"]))
            try: result = organizer.health_check()
            finally: organizer.close()
            return self.json(result)
        if path == "/api/live-migration":
            account = self.authenticated()
            if not account: return
            organizer = Organizer(tenant_config(account["username"]))
            try: items = organizer.live_migration_plan()
            finally: organizer.close()
            return self.json({"total": len(items), "items": items[:200]})
        if path.startswith("/api/photo-info/"):
            account = self.authenticated()
            if not account: return
            try: photo_id = int(path.rsplit("/", 1)[1])
            except ValueError: return self.json({"error": "Invalid photo"}, 400)
            config = tenant_config(account["username"]); db = Database(config.db_path)
            try: row = db.photo_by_id(photo_id)
            finally: db.close()
            if not row: return self.json({"error": "Not found"}, 404)
            source = self.library_source(config, row["current_path"])
            if not source.is_file(): return self.json({"error": "File missing"}, 404)
            return self.json({"exif": exif_details(source), "file": dict(row), "live": bool(row["live_partner_id"])})
        if path.startswith("/api/live-video/"):
            account = self.authenticated()
            if not account: return
            try: photo_id = int(path.rsplit("/", 1)[1])
            except ValueError: return self.json({"error": "Invalid photo"}, 400)
            config = tenant_config(account["username"])
            db = Database(config.db_path)
            try:
                row = db.photo_by_id(photo_id)
                partner = db.photo_by_id(row["live_partner_id"]) if row and row["live_partner_id"] else None
            finally: db.close()
            if not partner or partner["extension"].lower() != ".mov": return self.json({"error": "Not a Live Photo"}, 404)
            video = self.library_source(tenant_config(account["username"]), partner["current_path"])
            if not video.is_file(): return self.json({"error": "File missing"}, 404)
            return self.media(video)
        if path.startswith("/api/thumbnail/"):
            account = self.authenticated()
            if not account: return
            try: photo_id = int(path.rsplit("/", 1)[1])
            except ValueError: return self.json({"error": "Invalid photo"}, 400)
            config = tenant_config(account["username"]); db = Database(config.db_path)
            try: row = db.photo_by_id(photo_id)
            finally: db.close()
            if not row: return self.json({"error": "Not found"}, 404)
            source = self.library_source(config, row["current_path"])
            if not source.is_file(): return self.json({"error": "File missing"}, 404)
            try: image = thumbnail(source, config.system_dir / "thumbnails" / f"{photo_id}-{row['sha256']}-srgb.jpg")
            except Exception as exc: return self.json({"error": str(exc)}, 500)
            return self.binary(image, "image/jpeg")
        if path.startswith("/api/preview/"):
            account = self.authenticated()
            if not account: return
            try: photo_id = int(path.rsplit("/", 1)[1])
            except ValueError: return self.json({"error": "Invalid photo"}, 400)
            config = tenant_config(account["username"]); db = Database(config.db_path)
            try: row = db.photo_by_id(photo_id)
            finally: db.close()
            if not row: return self.json({"error": "Not found"}, 404)
            source = self.library_source(config, row["current_path"])
            if not source.is_file(): return self.json({"error": "File missing"}, 404)
            try: image = thumbnail(source, config.system_dir / "previews" / f"{photo_id}-{row['sha256']}-srgb.jpg", 1600)
            except Exception as exc: return self.json({"error": str(exc)}, 500)
            return self.binary(image, "image/jpeg")
        if path.startswith("/api/inbox-thumbnail/"):
            account = self.authenticated()
            if not account: return
            try: item_index = int(path.rsplit("/", 1)[1])
            except ValueError: return self.json({"error": "Invalid item"}, 400)
            item = user_state(account["username"]).item_at(item_index)
            if not item: return self.json({"error": "Not found"}, 404)
            config = tenant_config(account["username"]); source = Path(item.source).resolve()
            try: source.relative_to(config.inbox.resolve())
            except ValueError: return self.json({"error": "Not allowed"}, 403)
            if not source.is_file(): return self.json({"error": "File missing"}, 404)
            stat = source.stat()
            key = hashlib.sha256(f"{source}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()
            try: image = thumbnail(source, config.system_dir / "inbox-thumbnails" / f"{key}-srgb.jpg", 160)
            except Exception as exc: return self.json({"error": str(exc)}, 500)
            return self.binary(image, "image/jpeg")
        if path == "/api/users":
            account = self.authenticated()
            if not account: return
            if account["role"] != "admin": return self.json({"error": "需要管理員權限"}, 403)
            users = AUTH.list_users()
            for user in users:
                library = tenant_config(user["username"]).library
                user["library_bytes"] = directory_size(library)
            return self.json({"users": users})
        target = WEB / ("index.html" if path == "/" else path.lstrip("/"))
        if not target.is_file() or WEB not in target.resolve().parents: return self.json({"error": "Not found"}, 404)
        body = target.read_bytes(); self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        try: self._post()
        except PermissionError: self.json({"error": "無權存取這個檔案"}, 403)
        except (ValueError, TypeError): self.json({"error": "無效的請求參數"}, 400)

    def _post(self):
        path = urlparse(self.path).path
        if path == "/api/login":
            body = self.body(); result = AUTH.login(str(body.get("username", "")), str(body.get("password", "")))
            if not result: return self.json({"error": "帳號或密碼錯誤"}, 401)
            cookie = f"photo_session={result.pop('token')}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"
            return self.json(result, cookie=cookie)
        if path == "/api/logout":
            AUTH.logout(self.token()); return self.json({"ok": True}, cookie="photo_session=; Path=/; Max-Age=0")
        account = self.authenticated(csrf=True)
        if not account: return
        if path == "/api/users":
            if account["role"] != "admin": return self.json({"error": "需要管理員權限"}, 403)
            body = self.body()
            try: user_id = AUTH.create_user(str(body.get("username", "")), str(body.get("password", "")), str(body.get("role", "user")))
            except Exception as exc: return self.json({"error": str(exc)}, 400)
            AUTH.audit(account["id"], "create_user", f"user_id={user_id}", self.client_address[0])
            return self.json({"id": user_id}, 201)
        if path in {"/api/user-active", "/api/user-password"}:
            if account["role"] != "admin": return self.json({"error": "需要管理員權限"}, 403)
            body = self.body(); user_id = int(body.get("id", 0))
            if path.endswith("active") and user_id == account["id"] and not bool(body.get("active")):
                return self.json({"error": "不可停用目前登入中的管理員"}, 400)
            try:
                changed = (AUTH.set_active(user_id, bool(body.get("active"))) if path.endswith("active")
                           else AUTH.reset_password(user_id, str(body.get("password", ""))))
            except Exception as exc: return self.json({"error": str(exc)}, 400)
            if not changed: return self.json({"error": "找不到使用者"}, 404)
            AUTH.audit(account["id"], path.rsplit("-", 1)[-1], f"user_id={user_id}", self.client_address[0])
            return self.json({"ok": True})
        if path == "/api/backup-restore":
            username = account["username"]; state = user_state(username)
            name = Path(str(self.body().get("name", ""))).name; config = tenant_config(username)
            source = config.backups_dir / name
            if not source.is_file() or source.parent.resolve() != config.backups_dir.resolve():
                return self.json({"error": "找不到備份"}, 404)
            with state.lock:
                if state.busy: return self.json({"error": "請等待目前工作完成"}, 409)
                state.busy = True
            state.update(phase="restore", message="正在還原索引", error=None)
            try:
                safety = restore_index_backup(config, source)
                state.update(items=[], message="索引已還原，請重新掃描 Inbox", progress=100)
            except Exception as exc:
                state.update(error=str(exc), message="還原失敗")
                return self.json({"error": str(exc)}, 400)
            finally: state.update(busy=False)
            AUTH.audit(account["id"], "restore_backup", f"source={name},safety={safety.name}", self.client_address[0])
            return self.json({"ok": True, "safety_backup": safety.name})
        if path == "/api/favorite":
            body = self.body(); config = tenant_config(account["username"]); db = Database(config.db_path)
            try: changed = db.set_favorite(int(body.get("id", 0)), bool(body.get("favorite")))
            finally: db.close()
            if not changed: return self.json({"error": "找不到照片"}, 404)
            return self.json({"ok": True})
        if path == "/api/download":
            body = self.body()
            if len(body.get("ids", [])) > 200: return self.json({"error": "每次最多下載 200 張，請減少選取數量"}, 400)
            ids = [int(x) for x in body.get("ids", [])]
            if not ids: return self.json({"error": "請先選擇照片"}, 400)
            config = tenant_config(account["username"]); db = Database(config.db_path)
            try: rows = db.photos_by_ids(ids)
            finally: db.close()
            descriptor, name = tempfile.mkstemp(prefix="photo-organizer-", suffix=".zip")
            os.close(descriptor)
            temporary = Path(name)
            try:
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_STORED) as archive:
                    for row in rows:
                        source = Path(row["current_path"])
                        if source.is_file():
                            try: name = str(source.resolve().relative_to(config.library.resolve()))
                            except ValueError: continue
                            archive.write(source, name)
                self.download(temporary, "photo-organizer-selection.zip")
            finally: temporary.unlink(missing_ok=True)
            return
        if path in {"/api/trash", "/api/trash-restore"}:
            body = self.body()
            if len(body.get("ids", [])) > 500: return self.json({"error": "每次最多處理 500 張，請分批選取"}, 400)
            ids = [int(x) for x in body.get("ids", [])]
            organizer = Organizer(tenant_config(account["username"]))
            try: completed, failed = (organizer.move_to_trash(ids) if path == "/api/trash" else organizer.restore_from_trash(ids))
            finally: organizer.close()
            AUTH.audit(account["id"], path.rsplit("/", 1)[1], f"completed={completed},failed={failed}", self.client_address[0])
            return self.json({"completed": completed, "failed": failed})
        if path == "/api/trash-purge":
            body = self.body()
            if body.get("confirm") is not True: return self.json({"error": "需要再次確認"}, 400)
            organizer = Organizer(tenant_config(account["username"]))
            try: purged, failed = organizer.purge_trash(int(body.get("days", 30)))
            finally: organizer.close()
            AUTH.audit(account["id"], "purge_trash", f"purged={purged},failed={failed}", self.client_address[0])
            return self.json({"purged": purged, "failed": failed})
        if path == "/api/albums":
            body = self.body(); name = str(body.get("name", "")).strip()
            if not name: return self.json({"error": "相簿名稱不可空白"}, 400)
            db = Database(tenant_config(account["username"]).db_path)
            try:
                try: album_id = db.create_album(name[:80], str(body.get("description", ""))[:500], now_iso())
                except Exception as exc: return self.json({"error": str(exc)}, 400)
            finally: db.close()
            return self.json({"id": album_id}, 201)
        if path.startswith("/api/album/") and path.endswith("/photos"):
            try: album_id = int(path.split("/")[3])
            except ValueError: return self.json({"error": "Invalid album"}, 400)
            body = self.body()
            if len(body.get("ids", [])) > 500: return self.json({"error": "每次最多加入 500 張，請分批選取"}, 400)
            ids = [int(x) for x in body.get("ids", [])]
            db = Database(tenant_config(account["username"]).db_path)
            try: added = db.add_to_album(album_id, ids, now_iso())
            finally: db.close()
            return self.json({"added": added})
        if path == "/api/settings":
            allowed = {"auto_scan", "auto_import", "auto_purge", "schedule_minutes", "trash_days", "notification_url"}
            body = self.body(); db = Database(tenant_config(account["username"]).db_path)
            try:
                for key, value in body.items():
                    if key in allowed:
                        if key in {"auto_scan", "auto_import", "auto_purge"}:
                            value = str(value).lower()
                            if value not in {"true", "false"}: return self.json({"error": "無效的開關設定"}, 400)
                        if key in {"schedule_minutes", "trash_days"}:
                            try: value = max(5 if key == "schedule_minutes" else 1, int(value))
                            except (ValueError, TypeError): return self.json({"error": "請輸入有效天數或分鐘數"}, 400)
                        db.set_setting(key, str(value), now_iso())
            finally: db.close()
            return self.json({"ok": True})
        username = account["username"]; state = user_state(username)
        if path == "/api/stop":
            if not state.busy: return self.json({"error": "目前沒有進行中的工作"}, 409)
            state.update(cancel_requested=True, message="正在安全停止…")
            return self.json({"accepted": True}, 202)
        jobs = {"/api/scan": ("scan", lambda: scan_user(username, state)),
                "/api/import": ("import", lambda: import_user(username, state)),
                "/api/reindex": ("reindex", lambda: maintenance(username, state, "reindex")),
                "/api/backup": ("backup", lambda: maintenance(username, state, "backup")),
                "/api/repair": ("repair", lambda: maintenance(username, state, "repair")),
                "/api/migrate-live": ("migrate-live", lambda: maintenance(username, state, "migrate-live")),
                "/api/recover": ("recover", lambda: maintenance(username, state, "recover"))}
        if path not in jobs: return self.json({"error": "Not found"}, 404)
        name, work = jobs[path]
        if not run_job(state, name, work, username): return self.json({"error": "另一項工作正在執行"}, 409)
        AUTH.audit(account["id"], name, f"user={username}", self.client_address[0]); self.json({"accepted": True}, 202)

    def log_message(self, format, *args): print(f"{self.address_string()} - {format % args}")


def main():
    global AUTH
    AUTH = AuthStore(SYSTEM_DATA / "system")
    password = os.environ.get("PHOTO_ADMIN_PASSWORD")
    if password: AUTH.bootstrap(os.environ.get("PHOTO_ADMIN_USER", "admin"), password)
    elif not AUTH.connection.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
        raise SystemExit("First start requires a non-empty PHOTO_ADMIN_PASSWORD")
    host = os.environ.get("PHOTO_HOST", "0.0.0.0"); port = int(os.environ.get("PHOTO_PORT", "8080"))
    Thread(target=scheduler_loop, daemon=True).start()
    print(f"Photo Organizer Service: http://{host}:{port}"); ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__": main()

from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.request import build_opener, HTTPCookieProcessor, Request
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from photo_organizer.database import Database

import photo_organizer.service as service
from photo_organizer.auth import AuthStore
from photo_organizer.config import Config
from photo_organizer.engine import PlanItem


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); root = Path(self.temp.name)
        self.previous = {key: os.environ.get(key) for key in
                         ("PHOTO_DATA", "PHOTO_INBOX_ROOT", "PHOTO_LIBRARY_ROOT", "PHOTO_BACKUP_ROOT")}
        os.environ.update({"PHOTO_DATA": str(root / "data"), "PHOTO_INBOX_ROOT": str(root / "inbox"),
                           "PHOTO_LIBRARY_ROOT": str(root / "library"), "PHOTO_BACKUP_ROOT": str(root / "backup")})
        service.SYSTEM_DATA = root / "data"; service.AUTH = AuthStore(service.SYSTEM_DATA / "system")
        service.AUTH.bootstrap("admin", "1")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); service.AUTH.close(); service.STATES.clear()
        for key, value in self.previous.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        self.temp.cleanup()

    def json_request(self, path, body=None, csrf=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if csrf: headers["X-CSRF-Token"] = csrf
        with self.opener.open(Request(self.base + path, data=data, headers=headers)) as response:
            return json.load(response)

    def test_authenticated_integrated_endpoints(self):
        login = self.json_request("/api/login", {"username": "admin", "password": "1"})
        csrf = login["csrf"]
        status = self.json_request("/api/status")
        self.assertIn("version", status["system"])
        album = self.json_request("/api/albums", {"name": "旅行"}, csrf)
        self.assertGreater(album["id"], 0)
        self.assertEqual(len(self.json_request("/api/albums")["items"]), 1)
        self.json_request("/api/settings", {"auto_scan": False, "schedule_minutes": 60}, csrf)
        self.assertEqual(self.json_request("/api/settings")["settings"]["auto_scan"], "false")
        health = self.json_request("/api/health-check")
        self.assertEqual(health["database"], "ok")
        self.assertEqual(self.json_request("/api/live-migration")["total"], 0)

    def test_job_state_and_scan_plan_survive_restart(self):
        root = Path(self.temp.name); config = Config(root / "persist-inbox", root / "persist-library",
                                                     index_dir=root / "persist-data")
        config.ensure_directories(); state = service.State(config)
        item = PlanItem(str(config.inbox / "x.jpg"), str(config.jpg_dir / "x.jpg"), "", "IMPORT",
                        "JPG", None, "test", source_size=1, source_mtime_ns=1, quick_hash="abc")
        state.update(busy=True, phase="moving", message="processing", items=[item])
        restored = service.State(config)
        self.assertEqual(restored.phase, "interrupted")
        self.assertEqual(len(restored.items), 1)
        self.assertEqual(restored.items[0].quick_hash, "abc")

    def test_over_limit_requests_fail_instead_of_truncating(self):
        csrf=self.json_request('/api/login',{'username':'admin','password':'1'})['csrf']
        for path,count in (('/api/download',201),('/api/trash',501),('/api/trash-restore',501)):
            with self.assertRaises(HTTPError) as error:
                self.json_request(path,{'ids':list(range(count))},csrf)
            self.assertEqual(error.exception.code,400)
            error.exception.close()

    def test_pagination_exposes_remaining_map_and_trash_rows(self):
        self.json_request('/api/login',{'username':'admin','password':'1'})
        config=service.tenant_config('admin');config.ensure_directories();db=Database(config.db_path)
        try:
            with db.transaction():
                for i in range(502):
                    photo=db.add_photo(sha256=f'{i:064x}',original_path='test',current_path=str(config.jpg_dir/f'{i}.jpg'),capture_date='2023-01-01',file_type='JPG',extension='.jpg',file_size=1,imported_at='now',status='ACTIVE')
                    db.connection.execute('UPDATE photos SET gps_latitude=25,gps_longitude=121 WHERE id=?',(photo,))
            first=self.json_request('/api/map');second=self.json_request('/api/map?offset=500')
            self.assertEqual((first['total'],len(first['items']),len(second['items'])),(502,500,2))
            self.assertFalse(set(i['id'] for i in first['items']) & set(i['id'] for i in second['items']))
            with db.transaction(): db.connection.execute("UPDATE photos SET status='TRASH',deleted_at='now'")
            self.assertEqual(len(self.json_request('/api/trash?offset=400')['items']),102)
            self.assertEqual(self.json_request('/api/trash')['total'],502)
        finally: db.close()

    def test_metadata_cannot_read_outside_tenant_library(self):
        self.json_request('/api/login',{'username':'admin','password':'1'})
        outside=Path(self.temp.name)/'outside.jpg';outside.write_bytes(b'private')
        db=Database(service.tenant_config('admin').db_path)
        try:
            with db.transaction():
                photo=db.add_photo(sha256='a'*64,original_path=str(outside),current_path=str(outside),capture_date=None,file_type='JPG',extension='.jpg',file_size=7,imported_at='now',status='ACTIVE')
        finally: db.close()
        for route in ('thumbnail','preview','photo-info'):
            with self.assertRaises(HTTPError) as error: self.json_request(f'/api/{route}/{photo}')
            self.assertEqual(error.exception.code,403);error.exception.close()

    def test_preview_is_paged_but_import_receives_all_items(self):
        root = Path(self.temp.name); config = Config(root / "page-inbox", root / "page-library",
                                                     index_dir=root / "page-data", backup_dir=root / "page-backup")
        config.ensure_directories(); state = service.State(config)
        items = [PlanItem(str(config.inbox / f"{i}.txt"), str(config.rejected_dir / f"{i}.txt"),
                          str(i), "OTHER", "OTHER", None, "test") for i in range(501)]
        state.update(items=items)
        self.assertEqual(len(state.snapshot(0)["items"]), 500)
        self.assertEqual(len(state.snapshot(500)["items"]), 1)
        class FakeOrganizer:
            def __init__(self): self.received = []
            def backup_index(self): return Path("backup.db")
            def execute(self, received, progress): self.received = received; return Path("manifest.jsonl")
            def close(self): pass
        fake = FakeOrganizer()
        with patch("photo_organizer.service.Organizer", return_value=fake):
            service.import_user("admin", state)
        self.assertEqual(len(fake.received), 501)

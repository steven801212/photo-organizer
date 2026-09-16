from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
import subprocess
import sys
import unittest

from photo_organizer.config import Config
from photo_organizer.database import Database
from photo_organizer.engine import Organizer, restore_index_backup, sha256_file
from photo_organizer.metadata import ExifToolSession, _creation_flags
from photo_organizer.storage_lock import storage_lock
import photo_organizer.service as service


class ReleaseSafetyTests(unittest.TestCase):
    def test_restore_waits_for_connections_and_blocks_new_connections(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); config = Config(root/'inbox', root/'library')
            organizer = Organizer(config); backup = organizer.backup_index(); organizer.close()
            reader_ready, release_reader, restoring, done = (Event() for _ in range(4))
            def hold_reader():
                db = Database(config.db_path)
                try:
                    db.set_setting('after_backup', 'true', 'now')
                    reader_ready.set()
                    if not release_reader.wait(5): raise TimeoutError('test reader release')
                finally: db.close()
            def restore():
                restoring.set()
                result = restore_index_backup(config, backup)
                done.set(); return result
            with ThreadPoolExecutor(max_workers=2) as pool:
                reader = pool.submit(hold_reader)
                self.assertTrue(reader_ready.wait(3))
                future = pool.submit(restore)
                self.assertTrue(restoring.wait(3))
                try: self.assertFalse(done.wait(.15), 'Restore must wait for an open connection')
                finally: release_reader.set()
                reader.result(5); self.assertTrue(future.result(5).is_file())
            with storage_lock(config.db_path).exclusive():
                # Existing connections are closed; new readers must wait.
                entered = Event()
                def new_reader():
                    db = Database(config.db_path)
                    try: entered.set(); return db.settings()
                    finally: db.close()
                pool = ThreadPoolExecutor(max_workers=1)
                future = pool.submit(new_reader)
                self.assertFalse(entered.wait(.15))
            try: self.assertNotIn('after_backup', future.result(5))
            finally: pool.shutdown()

    def test_two_organizers_serialize_filesystem_operations(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); config=Config(root/'inbox',root/'library')
            first=Organizer(config); entered=Event()
            def another():
                o=Organizer(config)
                try: entered.set()
                finally: o.close()
            pool=ThreadPoolExecutor(max_workers=1); future=pool.submit(another)
            try: self.assertFalse(entered.wait(.15))
            finally: first.close()
            try: future.result(5); self.assertTrue(entered.is_set())
            finally: pool.shutdown()

    def test_user_state_is_loaded_once(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); config=Config(root/'inbox',root/'library'); config.ensure_directories()
            with patch.object(service, 'STATES', {}), patch.object(service, 'tenant_config', return_value=config), patch.object(service, 'State', wraps=service.State) as constructor:
                first=service.user_state('test')
                for _ in range(25): self.assertIs(service.user_state('test'), first)
                self.assertEqual(constructor.call_count, 1)

    def test_exif_timeout_kills_process_and_next_request_recovers(self):
        session=ExifToolSession()
        stalled="import time; time.sleep(60)"
        healthy="import sys\nfor line in sys.stdin:\n if line.startswith('-execute'):\n  print('[]'); print('{ready'+line.strip()[8:]+'}', flush=True)\n if line.strip()=='False': break\n"
        def start(code):
            session.process=subprocess.Popen([sys.executable,'-u','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=_creation_flags())
        try:
            with patch.object(session,'_start',side_effect=lambda:start(stalled)):
                with self.assertRaises(TimeoutError): session.json(Path('test.jpg'), (), timeout=.2)
            self.assertIsNone(session.process)
            with patch.object(session,'_start',side_effect=lambda:start(healthy)):
                self.assertEqual(session.json(Path('test.jpg'), (), timeout=3), [])
        finally: session.close()

    def test_publish_never_replaces_existing_file_in_either_mode(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); o=Organizer(Config(root/'inbox',root/'library'))
            try:
                source=o.config.inbox/'x.jpg'; source.write_bytes(b'new')
                destination=o.config.jpg_dir/'x.jpg'; destination.write_bytes(b'old')
                digest=sha256_file(source)
                for method in (o._safe_place,o._safe_copy):
                    with self.assertRaises(FileExistsError): method(source,destination,digest)
                    self.assertEqual(destination.read_bytes(),b'old')
                    self.assertEqual(source.read_bytes(),b'new')
            finally: o.close()

    def test_reindex_metadata_runs_outside_write_transaction(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); o=Organizer(Config(root/'inbox',root/'library'))
            from photo_organizer.metadata import index_metadata
            empty={k:None for k in ('camera_make','camera_model','lens_model','focal_length','aperture','shutter_speed','iso','gps_latitude','gps_longitude','content_identifier')}
            try:
                (o.config.jpg_dir/'photo.jpg').write_bytes(b'photo')
                def metadata(_):
                    self.assertFalse(o.db.connection.in_transaction)
                    return empty
                with patch('photo_organizer.engine.index_metadata',side_effect=metadata),patch('photo_organizer.engine.capture_date',return_value=(datetime(2023,2,4),'test')):
                    o.reindex()
                    o.db.connection.execute('UPDATE photos SET metadata_indexed_at=NULL'); o.db.connection.commit()
                    o.reindex()
            finally: o.close()

    def test_purge_of_duplicate_reference_keeps_database_consistent(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);o=Organizer(Config(root/'inbox',root/'library',stable_interval=0))
            try:
                source=o.config.inbox/'a.jpg';source.write_bytes(b'photo')
                with patch('photo_organizer.engine.capture_date',return_value=(datetime(2023,2,4),'test')):
                    o.execute(o.scan());source.write_bytes(b'photo');o.execute(o.scan())
                row=o.db.find_photo(sha256_file(next(o.config.jpg_dir.rglob('*.jpg'))))
                self.assertEqual(o.move_to_trash([row['id']]),(1,0))
                with o.db.transaction():o.db.connection.execute("UPDATE photos SET deleted_at='2000-01-01T00:00:00+00:00'")
                self.assertEqual(o.purge_trash(1),(1,0))
                self.assertEqual(o.db.connection.execute('PRAGMA foreign_key_check').fetchall(),[])
            finally:o.close()

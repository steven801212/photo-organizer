from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from photo_organizer.auth import AuthStore


class AuthTests(unittest.TestCase):
    def test_non_empty_short_password_is_allowed(self):
        with TemporaryDirectory() as temp:
            store = AuthStore(Path(temp))
            store.bootstrap("admin", "1")
            self.assertIsNotNone(store.login("admin", "1"))
            store.close()

    def test_accounts_and_sessions_are_separate_from_photo_database(self):
        with TemporaryDirectory() as temp:
            store = AuthStore(Path(temp))
            store.bootstrap("admin", "a-secure-password")
            store.create_user("steve", "another-secure-password")
            session = store.login("steve", "another-secure-password")
            self.assertEqual(session["username"], "steve")
            self.assertEqual(store.session(session["token"])["username"], "steve")
            self.assertIsNone(store.login("steve", "wrong-password"))
            # Existing installations must not be blocked by a changed or
            # placeholder bootstrap password during a container update.
            store.bootstrap("bad user", "short")
            store.close()

    def test_admin_can_disable_and_reset_user_without_touching_other_accounts(self):
        with TemporaryDirectory() as temp:
            store = AuthStore(Path(temp)); store.bootstrap("admin", "a")
            user_id = store.create_user("steve", "old")
            self.assertTrue(store.set_active(user_id, False))
            self.assertIsNone(store.login("steve", "old"))
            self.assertTrue(store.set_active(user_id, True))
            self.assertTrue(store.reset_password(user_id, "new"))
            self.assertIsNone(store.login("steve", "old"))
            self.assertIsNotNone(store.login("steve", "new"))
            self.assertIsNotNone(store.login("admin", "a"))
            store.close()

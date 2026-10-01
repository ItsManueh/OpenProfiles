from __future__ import annotations

import hashlib
import http.server
import json
import threading
import unittest
import zipfile
from typing import ClassVar, cast

import backup
import profiles
import settings
import updater
from about import is_newer, version_key
from profiles import ProfileError
from tests.support import DataFolderTest


class Versions(unittest.TestCase):
    def test_compare(self) -> None:
        self.assertEqual(version_key("v0.2.1"), (0, 2, 1))
        self.assertTrue(is_newer("v0.10.0", "0.9.9"))
        self.assertFalse(is_newer("v0.2.0", "0.2.0"))


class Settings(DataFolderTest):
    def test_defaults_and_round_trip(self) -> None:
        self.assertEqual(settings.load_settings(), settings.Settings())
        saved = settings.Settings(mode="dark", sounds=False, restore_tabs=False, window="abc")
        settings.save_settings(saved)
        self.assertEqual(settings.load_settings(), saved)

    def test_invalid_values_are_ignored(self) -> None:
        settings.SETTINGS_FILE.write_text(json.dumps({"mode": "neon", "sounds": "yes", "window": 3, "tray": True}))
        self.assertEqual(settings.load_settings(), settings.Settings())


class Backups(DataFolderTest):
    def test_export_and_import(self) -> None:
        first = self.create("shop", notes="main", color="green")
        (first.folder / "Default").mkdir()
        (first.folder / "Default" / "Cookies").write_text("cookies")
        (first.folder / "Default" / "Cache").mkdir()
        (first.folder / "Default" / "Cache" / "big").write_text("x" * 1000)
        path = self.folder / "backup.zip"
        count, _size = backup.export_profiles(path)
        self.assertEqual(count, 1)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        self.assertIn("profiles/shop/Default/Cookies", names)
        self.assertNotIn("profiles/shop/Default/Cache/big", names)  # caches are left out

        imported = backup.import_profiles(path)  # "shop" exists: it comes in with another name
        self.assertEqual(imported, ["shop_imported"])
        copy = profiles.load_profiles()["shop_imported"]
        self.assertEqual((copy.notes, copy.color, copy.seed), ("main", "green", first.seed))
        self.assertEqual((copy.folder / "Default" / "Cookies").read_text(), "cookies")

    def test_unsafe_backups_are_refused(self) -> None:
        cases = {
            "path": ({"name": "a", "device": ""}, "profiles/a/../../evil.txt"),
            "name": ({"name": "../evil", "device": ""}, "profiles/x/file"),
        }
        for label, (profile, member) in cases.items():
            with self.subTest(case=label):
                path = self.folder / f"{label}.zip"
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(backup.MANIFEST, json.dumps({"profiles": [profile]}))
                    archive.writestr(member, "x")
                with self.assertRaises(ProfileError):
                    backup.import_profiles(path)
                self.assertFalse((self.folder / "evil.txt").exists())
                self.assertEqual(profiles.load_profiles(), {})
                self.assertFalse((profiles.PROFILES_DIR / "a").exists())  # nothing half-imported

    def test_not_a_backup(self) -> None:
        path = self.folder / "other.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("readme.txt", "hello")
        with self.assertRaises(ProfileError):
            backup.import_profiles(path)


class Updates(DataFolderTest):
    payload = b"new version" * 1000
    checksum = hashlib.sha256(payload).hexdigest()
    server: ClassVar[http.server.ThreadingHTTPServer]

    @classmethod
    def setUpClass(cls) -> None:
        test = cls

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                port = cast("tuple[str, int]", self.server.server_address)[1]
                base = f"http://127.0.0.1:{port}"
                if self.path == "/api":
                    assets = [
                        {"name": "OpenProfiles.exe", "browser_download_url": base + "/exe"},
                        {"name": "OpenProfiles.exe.sha256", "browser_download_url": base + "/sha"},
                    ]
                    body = json.dumps({"tag_name": "v9.0.0", "assets": assets}).encode()
                elif self.path == "/exe":
                    body = test.payload
                else:
                    body = f"{test.checksum}  OpenProfiles.exe".encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # same names as the base class
                pass

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def api(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/api"

    def test_download_is_checked(self) -> None:
        path = updater.download_update(api_url=self.api(), folder=self.folder)
        self.assertEqual(path.read_bytes(), self.payload)

    def test_wrong_checksum_is_discarded(self) -> None:
        original = Updates.checksum
        Updates.checksum = "0" * 64
        try:
            with self.assertRaises(updater.UpdateError):
                updater.download_update(api_url=self.api(), folder=self.folder)
        finally:
            Updates.checksum = original
        self.assertFalse(list(self.folder.glob("*.download")))


if __name__ == "__main__":
    unittest.main()

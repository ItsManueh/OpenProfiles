from __future__ import annotations

import json
import sys
import unittest

import browser_session
import launcher
import profiles
import secure_store
import stealth
from profiles import Profile
from tests.support import DEVICES, DataFolderTest


class AntiDetection(unittest.TestCase):
    def test_languages_and_accept_language(self) -> None:
        self.assertEqual(stealth.languages("es-ES", "chromium", "desktop"), ["es-ES", "es"])
        self.assertEqual(stealth.languages("es-ES", "webkit", "iphone"), ["es-ES"])  # Safari lists one
        self.assertEqual(stealth.accept_language(["es-ES", "es"]), "es-ES,es;q=0.9")
        self.assertEqual(stealth.accept_language(["es-ES"]), "es-ES,es;q=0.9")

    def test_iphone_screens(self) -> None:
        self.assertEqual(stealth.iphone_screen(DEVICES["iPhone 15 Pro"]), {"width": 393, "height": 852})
        self.assertEqual(
            stealth.iphone_screen({"viewport": {"width": 375, "height": 553}}), {"width": 375, "height": 667}
        )
        self.assertEqual(
            stealth.iphone_screen({"viewport": {"width": 375, "height": 635}}), {"width": 375, "height": 812}
        )

    def test_generated_desktop_user_agents_follow_the_engine(self) -> None:
        old = Profile(
            "a",
            "",
            mode="desktop",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        )
        self.assertEqual(stealth.user_agent_for(old), profiles.desktop_user_agent())
        custom = Profile("b", "", mode="desktop", user_agent="My browser/1.0")
        self.assertEqual(stealth.user_agent_for(custom), "My browser/1.0")

    def test_seed_is_stable_and_differs_between_profiles(self) -> None:
        a, b = Profile("a", "", seed="1"), Profile("b", "", seed="2")
        self.assertEqual(stealth.seed_of(a), stealth.seed_of(Profile("other", "", seed="1")))
        self.assertNotEqual(stealth.seed_of(a), stealth.seed_of(b))
        self.assertEqual(stealth.seed_of(Profile("n", "")), stealth.seed_of(Profile("n", "")))

    def test_init_script_configuration(self) -> None:
        phone = Profile("p", "iPhone 15 Pro", engine="chromium", mode="iphone", seed="x")
        config = stealth.config_for(phone, DEVICES["iPhone 15 Pro"])
        self.assertEqual(config["iphone"], {"screen": [393, 852], "pixelRatio": 3, "chromium": True})
        self.assertFalse(config["webdriver"])  # Chromium: done by its flags
        webkit = stealth.config_for(Profile("w", "iPhone 15 Pro", mode="iphone", seed="x"), DEVICES["iPhone 15 Pro"])
        self.assertTrue(webkit["webdriver"] and webkit["blockPopups"])
        script = stealth.init_script(phone, DEVICES["iPhone 15 Pro"])
        self.assertNotIn("__CONFIG__", script)
        self.assertIn(json.dumps(config), script)

    def test_launch_options(self) -> None:
        desktop = launcher.launch_options(Profile("d", "", mode="desktop", engine="chromium"), DEVICES)
        self.assertEqual(desktop["ignore_default_args"], stealth.CHROMIUM_IGNORED_ARGS)
        self.assertIn("--disable-blink-features=AutomationControlled", desktop["args"])
        self.assertTrue(any(arg.startswith("--window-size=") for arg in desktop["args"]))
        self.assertTrue(desktop["accept_downloads"])
        phone = launcher.launch_options(Profile("p", "iPhone 15 Pro", mode="iphone", engine="webkit"), DEVICES)
        self.assertEqual(phone["screen"], {"width": 393, "height": 852})
        self.assertNotIn("ignore_default_args", phone)


class Session(DataFolderTest):
    def test_encryption_round_trip(self) -> None:
        path = self.folder / "secret.bin"
        secure_store.write(path, b"session=abc")
        self.assertEqual(secure_store.read(path), b"session=abc")
        if sys.platform == "win32":
            self.assertNotIn(b"abc", path.read_bytes())
        self.assertIsNone(secure_store.read(self.folder / "missing.bin"))

    def test_unreadable_session_is_ignored(self) -> None:
        (self.folder / browser_session.SESSION_FILE).write_bytes(b"not encrypted")
        self.assertEqual(browser_session.load_session(self.folder), browser_session.SavedSession())

    def test_saved_session_is_read_back(self) -> None:
        content = {"version": 1, "cookies": [{"name": "s", "value": "1"}, "bad"], "tabs": ["https://a.com/", 3]}
        secure_store.write(self.folder / browser_session.SESSION_FILE, json.dumps(content).encode())
        saved = browser_session.load_session(self.folder)
        self.assertEqual(saved.cookies, [{"name": "s", "value": "1"}])
        self.assertEqual(saved.tabs, ["https://a.com/"])

    def test_safe_url_drops_query_and_fragment(self) -> None:
        self.assertEqual(browser_session.safe_url("https://site.com/path?token=1#x"), "https://site.com/path")

    def test_unique_download_names(self) -> None:
        (self.folder / "photo.jpg").write_text("1")
        self.assertEqual(browser_session.unique_path(self.folder, "photo.jpg").name, "photo (1).jpg")
        self.assertEqual(browser_session.unique_path(self.folder, 'a<b>:"c".txt').name, "a_b___c_.txt")

    def test_clean_exit_mark(self) -> None:
        preferences = self.folder / "Default" / "Preferences"
        preferences.parent.mkdir()
        preferences.write_text(json.dumps({"profile": {"exit_type": "Crashed", "name": "x"}}))
        browser_session.mark_clean_exit(self.folder)
        saved = json.loads(preferences.read_text())["profile"]
        self.assertEqual((saved["exit_type"], saved["exited_cleanly"], saved["name"]), ("Normal", True, "x"))


class DownloadProgress(unittest.TestCase):
    def test_installer_output(self) -> None:
        progress = launcher.DownloadProgress()
        lines = [
            "Downloading Chrome for Testing 140.0.7339.16 (playwright chromium v1187) from https://x/y.zip",
            "|■■■■■■■■                    |  40% of 147.3 MiB",
            "Downloading FFmpeg (playwright ffmpeg v1011) from https://x/z.zip",
        ]
        self.assertEqual(
            [progress.feed(line) for line in lines],
            [
                "Downloading Chrome for Testing…",
                "Downloading Chrome for Testing… 40 % of 147.3 MiB",
                "Downloading FFmpeg…",
            ],
        )
        self.assertIsNone(progress.feed("Chrome for Testing downloaded to C:\\x"))


class Sanity(DataFolderTest):
    def test_profile_folder_is_inside_the_temporary_data(self) -> None:
        self.assertTrue(str(Profile("x", "").folder).startswith(str(self.folder)))


if __name__ == "__main__":
    unittest.main()

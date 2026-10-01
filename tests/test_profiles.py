from __future__ import annotations

import json
import unittest

import profiles
from profiles import Profile, ProfileError
from tests.support import DEVICES, DataFolderTest


class CreateAndEdit(DataFolderTest):
    def test_create_fills_in_device_user_agent_and_seed(self) -> None:
        phone = self.create("phone", mode="iphone")
        self.assertIn(phone.device, DEVICES)
        self.assertIn("iPhone OS", phone.user_agent)
        self.assertEqual(len(phone.seed), 16)
        self.assertTrue(phone.folder.is_dir())

    def test_desktop_user_agent_matches_the_engine(self) -> None:
        desktop = self.create("desk")
        self.assertEqual(desktop.engine, "chromium")
        self.assertIn(f"Chrome/{profiles.chromium_major_version()}.0.0.0", desktop.user_agent)
        self.assertNotIn("Edg/", desktop.user_agent)

    def test_names_are_validated(self) -> None:
        self.create("account")
        for bad in ("Account", "has space", "nul", "x" * 41, ""):
            with self.subTest(name=bad), self.assertRaises(ProfileError):
                self.create(bad)

    def test_editing_keeps_the_fingerprint_and_last_opened(self) -> None:
        original = self.create("one", notes="first")
        profiles.mark_opened("one")
        opened = profiles.load_profiles()["one"].last_opened
        edited = profiles.update_profile("one", Profile(name="two", device="", notes="  changed   notes "), DEVICES)
        self.assertEqual(edited.seed, original.seed)
        self.assertEqual(edited.last_opened, opened)
        self.assertEqual(edited.notes, "changed notes")
        self.assertTrue((profiles.PROFILES_DIR / "two").is_dir())
        self.assertFalse((profiles.PROFILES_DIR / "one").exists())

    def test_invalid_color_is_rejected_and_sanitized(self) -> None:
        with self.assertRaises(ProfileError):
            self.create("c", color="brown")
        self.create("d", color="blue")
        data = json.loads(profiles.PROFILES_FILE.read_text(encoding="utf-8"))
        data["profiles"][0]["color"] = "brown"
        profiles.PROFILES_FILE.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(profiles.load_profiles()["d"].color, "")


class Storage(DataFolderTest):
    def test_older_profiles_get_a_seed_once(self) -> None:
        profiles.PROFILES_FILE.write_text(json.dumps({"profiles": [{"name": "old", "device": ""}]}), encoding="utf-8")
        first = profiles.load_profiles()["old"].seed
        self.assertTrue(first)
        self.assertEqual(profiles.load_profiles()["old"].seed, first)

    def test_damaged_file_is_restored_from_the_backup(self) -> None:
        self.create("a")
        self.create("b")
        profiles.PROFILES_FILE.write_text("{damaged", encoding="utf-8")
        self.assertEqual(list(profiles.load_profiles()), ["a", "b"])
        self.assertTrue(list(self.folder.glob("profiles.damaged-*.json")))

    def test_damaged_file_without_backup_raises(self) -> None:
        profiles.PROFILES_FILE.write_text("{damaged", encoding="utf-8")
        with self.assertRaises(ProfileError):
            profiles.load_profiles()

    def test_reorder(self) -> None:
        for name in ("a", "b", "c"):
            self.create(name)
        profiles.reorder_profiles(["c", "a", "b"])
        self.assertEqual(list(profiles.load_profiles()), ["c", "a", "b"])
        with self.assertRaises(ProfileError):
            profiles.reorder_profiles(["a", "b"])


class DeleteAndDuplicate(DataFolderTest):
    def test_delete_can_be_undone_at_its_place(self) -> None:
        for name in ("a", "b", "c"):
            self.create(name)
        (profiles.PROFILES_DIR / "b" / "cookies").write_text("session")
        deleted = profiles.delete_profile("b", undoable=True)
        self.assertEqual(list(profiles.load_profiles()), ["a", "c"])
        self.assertFalse((profiles.PROFILES_DIR / "b").exists())
        profiles.restore_profile(deleted)
        self.assertEqual(list(profiles.load_profiles()), ["a", "b", "c"])
        self.assertEqual((profiles.PROFILES_DIR / "b" / "cookies").read_text(), "session")

    def test_delete_is_permanent(self) -> None:
        gone = self.create("gone")
        profiles.delete_profile("gone")
        self.assertEqual(profiles.load_profiles(), {})
        self.assertFalse(gone.folder.exists())
        self.assertEqual(list(profiles.trash_dir().iterdir()), [])

    def test_purge_and_leftovers(self) -> None:
        self.create("x")
        self.create("y")
        deleted = profiles.delete_profile("x", undoable=True)
        profiles.purge(deleted)
        self.assertFalse(deleted.folder and deleted.folder.exists())
        profiles.delete_profile("y", undoable=True)  # the app closed before it could be undone
        profiles.empty_trash()
        self.assertEqual(list(profiles.trash_dir().iterdir()), [])

    def test_restore_fails_if_the_name_was_taken(self) -> None:
        self.create("same")
        deleted = profiles.delete_profile("same", undoable=True)
        self.create("same")
        with self.assertRaises(ProfileError):
            profiles.restore_profile(deleted)

    def test_duplicate_has_its_own_fingerprint_and_no_session(self) -> None:
        source = self.create("acc", mode="iphone", notes="shop", color="pink", locale="en-US")
        (source.folder / "cookies").write_text("session")
        copy = profiles.duplicate_profile("acc", DEVICES)
        self.assertEqual(copy.name, "acc_copy")
        self.assertNotEqual(copy.seed, source.seed)
        self.assertEqual((copy.notes, copy.color, copy.locale, copy.mode), ("shop", "pink", "en-US", "iphone"))
        self.assertFalse((copy.folder / "cookies").exists())
        self.assertEqual(profiles.duplicate_profile("acc", DEVICES).name, "acc_copy2")


if __name__ == "__main__":
    unittest.main()

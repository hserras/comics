import tempfile
import unittest
from pathlib import Path

from history import STATUS_DOWNLOADED, History
from library import adopt_existing_files, existing_file_for, migrate_library, plan_destination
from titles import UNSORTED_DIR, parse_title, plan_names


class ParseTitleTests(unittest.TestCase):
    def test_real_titles(self):
        cases = {
            "Black Panther #09 (2017)": ("Black Panther", "009", "2017"),
            "Uncanny X-Men #016 (2017)": ("Uncanny X-Men", "016", "2017"),
            "Spider-Man #3": ("Spider-Man", "003", None),
            "Venom War – Carnage #3": ("Venom War - Carnage", "003", None),
            "Spider-Man – The Spider’s Shadow #2": ("Spider-Man - The Spider's Shadow", "002", None),
            "Guardians of the Galaxy: Awesome Mix Infinite Comic #4": (
                "Guardians of the Galaxy Awesome Mix Infinite Comic", "004", None,
            ),
            "Heroes Reborn – Hyperion & the Imperial Squad #1": (
                "Heroes Reborn - Hyperion & the Imperial Squad", "001", None,
            ),
            "Series #0.1 (of 5) (2020)": ("Series", "000.1", "2020"),
        }
        for title, (series, number, year) in cases.items():
            with self.subTest(title=title):
                parsed = parse_title(title)
                self.assertEqual((parsed.series, parsed.number, parsed.year), (series, number, year))

    def test_stem(self):
        self.assertEqual(parse_title("Doctor Strange #015 (2017)").stem, "Doctor Strange #015 (2017)")
        self.assertEqual(parse_title("Hercules #6").stem, "Hercules #006")

    def test_ambiguous_titles_are_unsorted(self):
        for title in ("Civil War II FCBD", "Captain America 75th Anniversary Magazine", "X #1 Variant Cover", "#5"):
            with self.subTest(title=title):
                self.assertIsNone(parse_title(title))
        self.assertEqual(plan_names("Civil War II FCBD"), (UNSORTED_DIR, "Civil War II FCBD"))


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.library = Path(self._tmp.name) / "library"
        self.base = self.library / "Marvel"
        self.base.mkdir(parents=True)
        self.history = History(Path(self._tmp.name) / "data" / "history.db", self.library)

    def _issue(self, slug, title):
        return {"article_url": f"https://getcomics.org/marvel/{slug}/", "title": title}

    def test_reuses_folder_with_different_case(self):
        (self.base / "black panther").mkdir()
        target_dir, stem = plan_destination(self.base, "Black Panther #1")
        self.assertEqual(target_dir.name, "black panther")
        self.assertEqual(stem, "Black Panther #001")

    def test_adopts_hand_copied_file_but_not_one_owned_by_another_issue(self):
        folder = self.base / "Hercules"
        folder.mkdir()
        copied = folder / "Hercules #006.cbz"
        copied.write_bytes(b"x")
        issue = self._issue("hercules-6", "Hercules #6")
        statuses: dict[str, str] = {}

        self.assertEqual(adopt_existing_files(self.history, self.base, [issue], statuses, "Pack"), 1)
        self.assertEqual(statuses[issue["article_url"]], STATUS_DOWNLOADED)

        other = self._issue("hercules-6-variant", "Hercules #6")
        self.assertIsNone(existing_file_for(self.history, self.base, other["title"], other["article_url"]))

    def test_migrate_moves_pack_files_and_updates_history(self):
        old_dir = self.base / "2017.05.03 Marvel Week+"
        old_dir.mkdir()
        old_file = old_dir / "Black.Panther.009.2017.cbz"
        old_file.write_bytes(b"x")
        issue = self._issue("black-panther-9", "Black Panther #09 (2017)")
        self.history.record(issue["article_url"], issue["title"], STATUS_DOWNLOADED, file_path=old_file)

        dry = migrate_library(self.history, self.base, apply=False)
        self.assertEqual([r[0] for r in dry], ["move"])
        self.assertTrue(old_file.exists())

        migrate_library(self.history, self.base, apply=True)
        new_file = self.base / "Black Panther" / "Black Panther #009 (2017).cbz"
        self.assertTrue(new_file.is_file())
        self.assertFalse(old_dir.exists())
        self.assertEqual(self.history.status_of(issue["article_url"]), STATUS_DOWNLOADED)
        self.assertEqual(migrate_library(self.history, self.base, apply=False)[0][0], "ok")

    def test_migrate_prunes_empty_dirs_but_keeps_folders_with_files(self):
        (self.base / "2026.11.18 Weekly Pack").mkdir()
        keep = self.base / "Outra" / "sub"
        keep.mkdir(parents=True)
        (keep / "x.cbz").write_bytes(b"x")

        migrate_library(self.history, self.base, apply=False)
        self.assertTrue((self.base / "2026.11.18 Weekly Pack").exists())

        migrate_library(self.history, self.base, apply=True)
        self.assertFalse((self.base / "2026.11.18 Weekly Pack").exists())
        self.assertTrue((keep / "x.cbz").exists())
        self.assertTrue(self.base.exists())

    def test_migrate_reports_conflict_instead_of_overwriting(self):
        old_dir = self.base / "Pack"
        old_dir.mkdir()
        old_file = old_dir / "a.cbz"
        old_file.write_bytes(b"old")
        occupied = self.base / "Hercules" / "Hercules #006.cbz"
        occupied.parent.mkdir()
        occupied.write_bytes(b"other")
        issue = self._issue("hercules-6", "Hercules #6")
        self.history.record(issue["article_url"], issue["title"], STATUS_DOWNLOADED, file_path=old_file)

        self.assertEqual(migrate_library(self.history, self.base, apply=True)[0][0], "conflict")
        self.assertEqual(occupied.read_bytes(), b"other")
        self.assertTrue(old_file.exists())


if __name__ == "__main__":
    unittest.main()

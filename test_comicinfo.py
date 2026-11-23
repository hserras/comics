import functools
import http.server
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from comicinfo import COMICINFO_NAME, add_comicinfo, build_comicinfo, tag_existing
from downloader import download_single_issue
from history import STATUS_DOWNLOADED, History
from library import tag_library

URL = "https://getcomics.org/marvel/black-panther-9-2016/"


def make_cbz(path: Path, entries: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for name, data in (entries or {"001.jpg": b"page1", "002.jpg": b"page2"}).items():
            zf.writestr(name, data)
    return path


def read_info(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as zf:
        return {child.tag: child.text for child in ET.fromstring(zf.read(COMICINFO_NAME))}


class BuildComicInfoTests(unittest.TestCase):
    def fields(self, title, url=URL):
        return {c.tag: c.text for c in ET.fromstring(build_comicinfo(title, url))}

    def test_fields_without_leading_zeros(self):
        self.assertEqual(
            self.fields("Black Panther #09 (2017)"),
            {"Series": "Black Panther", "Number": "9", "Year": "2017", "Publisher": "Marvel", "Web": URL},
        )
        self.assertEqual(self.fields("Series #0.1 (of 5) (2020)")["Number"], "0.1")

    def test_missing_year_is_omitted_and_special_characters_are_escaped(self):
        fields = self.fields("Heroes Reborn – Hyperion & the Imperial Squad #1")
        self.assertNotIn("Year", fields)
        self.assertEqual(fields["Series"], "Heroes Reborn - Hyperion & the Imperial Squad")

    def test_unrecognised_title_gets_no_metadata(self):
        self.assertIsNone(build_comicinfo("Civil War II FCBD", URL))


class AddComicInfoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.xml = build_comicinfo("Hercules #6 (2016)", URL)

    def test_adds_metadata_and_keeps_the_pages(self):
        cbz = make_cbz(self.dir / "a.cbz")
        self.assertTrue(add_comicinfo(cbz, self.xml))
        with zipfile.ZipFile(cbz) as zf:
            self.assertIsNone(zf.testzip())
            self.assertEqual(zf.read("001.jpg"), b"page1")
            self.assertEqual(zf.namelist().count(COMICINFO_NAME), 1)
        self.assertEqual(read_info(cbz)["Series"], "Hercules")

    def test_never_overwrites_existing_metadata(self):
        cbz = make_cbz(self.dir / "a.cbz", {"001.jpg": b"p", "comicinfo.XML": b"<ComicInfo/>"})
        before = cbz.read_bytes()
        self.assertFalse(add_comicinfo(cbz, self.xml))
        self.assertEqual(cbz.read_bytes(), before)

    def test_leaves_files_that_are_not_zip_untouched(self):
        for name, content in (("rar.cbz", b"Rar!\x1a\x07" + b"0" * 100), ("broken.cbz", b"PK\x03\x04" + b"0" * 100)):
            path = self.dir / name
            path.write_bytes(content)
            with self.subTest(name=name):
                self.assertFalse(add_comicinfo(path, self.xml))
                self.assertEqual(path.read_bytes(), content)

    def test_tag_existing_replaces_via_copy_and_leaves_no_temp_files(self):
        cbz = make_cbz(self.dir / "a.cbz")
        self.assertTrue(tag_existing(cbz, self.xml))
        self.assertEqual(read_info(cbz)["Number"], "6")
        self.assertEqual([p.name for p in self.dir.iterdir()], ["a.cbz"])

    def test_tag_existing_ignores_other_extensions(self):
        cbr = make_cbz(self.dir / "a.cbr")
        before = cbr.read_bytes()
        self.assertFalse(tag_existing(cbr, self.xml))
        self.assertEqual(cbr.read_bytes(), before)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class DownloaderComicInfoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.served = root / "served"
        self.served.mkdir()
        self.dest = root / "dest"
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(self.served))
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base_url = f"http://127.0.0.1:{server.server_port}"

    def download(self, filename, **kwargs):
        return download_single_issue(
            "t", f"{self.base_url}/{filename}", self.dest, use_aria2c=False,
            target_stem="Hercules #006 (2016)", comicinfo=build_comicinfo("Hercules #6 (2016)", URL), **kwargs,
        )

    def test_cbz_is_tagged_before_the_final_rename(self):
        make_cbz(self.served / "x.cbz")
        ok, result = self.download("x.cbz")
        self.assertTrue(ok, result)
        self.assertEqual(Path(result).name, "Hercules #006 (2016).cbz")
        self.assertEqual(read_info(Path(result))["Series"], "Hercules")
        self.assertEqual([p.name for p in self.dest.iterdir()], ["Hercules #006 (2016).cbz"])

    def test_cbr_and_fake_cbz_are_saved_unchanged(self):
        rar = b"Rar!\x1a\x07" + b"0" * 5000
        (self.served / "x.cbr").write_bytes(rar)
        (self.served / "y.cbz").write_bytes(rar)
        for name in ("x.cbr", "y.cbz"):
            with self.subTest(name=name):
                ok, result = self.download(name, overwrite=True)
                self.assertTrue(ok, result)
                self.assertEqual(Path(result).read_bytes(), rar)


class TagLibraryTests(unittest.TestCase):
    def test_dry_run_changes_nothing_and_apply_tags_only_what_can_be_tagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            library.mkdir()
            history = History(Path(tmp) / "data" / "history.db", library)

            good = make_cbz(library / "Hercules #006.cbz")
            rar = library / "Spider-Man #003.cbr"
            rar.write_bytes(b"Rar!" + b"0" * 50)
            history.record(URL + "1", "Hercules #6", STATUS_DOWNLOADED, file_path=good)
            history.record(URL + "2", "Spider-Man #3", STATUS_DOWNLOADED, file_path=rar)
            before = good.read_bytes()

            dry = dict((rel, kind) for kind, rel in tag_library(history, apply=False))
            self.assertEqual(dry, {"Hercules #006.cbz": "tag", "Spider-Man #003.cbr": "skip"})
            self.assertEqual(good.read_bytes(), before)

            tag_library(history, apply=True)
            self.assertEqual(read_info(good)["Number"], "6")
            self.assertEqual(rar.read_bytes(), b"Rar!" + b"0" * 50)
            self.assertEqual({kind for kind, _ in tag_library(history, apply=True)}, {"skip"})


if __name__ == "__main__":
    unittest.main()

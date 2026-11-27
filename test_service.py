import tempfile
import unittest
from pathlib import Path
from unittest import mock

import service
from history import STATUS_DOWNLOADED, STATUS_FAILED, STATUS_MANUAL, History
from service import ComicService


def issue(slug, title):
    return {"title": title, "article_url": f"https://getcomics.org/marvel/{slug}/"}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.library = Path(self._tmp.name) / "library"
        self.base = self.library / "Marvel"
        self.base.mkdir(parents=True)
        self.history = History(Path(self._tmp.name) / "data" / "history.db", self.library)
        self.service = ComicService(self.history, self.base, use_aria2c=False, delay_range=(0, 0))
        self.events = []

    def _download(self, issues):
        return self.service.download("Pack", issues, self.events.append)

    def _kinds(self):
        return [e.kind for e in self.events]

    def test_select_issues(self):
        issues = [issue("a", "A #1"), issue("b", "B #2"), issue("c", "C #3")]
        statuses = {issues[0]["article_url"]: STATUS_DOWNLOADED}
        select = ComicService.select_issues
        self.assertEqual(select(issues, statuses, "pending"), issues[1:])
        self.assertEqual(select(issues, statuses, "all"), issues)
        self.assertEqual(select(issues, statuses, [2, 0]), [issues[2], issues[0]])

    def test_successful_download_is_recorded(self):
        item = issue("a", "Spider-Man #1 (2020)")

        def fake_download(**kwargs):
            kwargs["on_progress"](50, 100)
            dest = kwargs["target_dir"] / f"{kwargs['target_stem']}.cbz"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")
            return True, str(dest)

        with mock.patch.object(service, "resolve_issue_download_links",
                               return_value=[{"url": "http://x/f", "label": "MAIN"}]), \
             mock.patch.object(service, "download_single_issue", side_effect=fake_download):
            result = self._download([item])

        self.assertEqual((result.succeeded, result.total, result.manual_links), (1, 1, []))
        self.assertEqual(self._kinds(), ["started", "progress", "saved"])
        self.assertEqual(self.events[-1].label, "MAIN")
        self.assertEqual(self.history.statuses([item["article_url"]])[item["article_url"]], STATUS_DOWNLOADED)

    def test_failed_candidates_fall_back_to_manual(self):
        item = issue("a", "Spider-Man #1 (2020)")
        candidates = [{"url": "http://x/1", "label": "A"}, {"url": "http://x/2", "label": "B"}]

        with mock.patch.object(service, "resolve_issue_download_links", return_value=candidates), \
             mock.patch.object(service, "download_single_issue",
                               side_effect=[(False, "http://host/1"), (False, "http://host/2")]):
            result = self._download([item])

        self.assertEqual(result.succeeded, 0)
        self.assertEqual(result.manual_links, [(item["title"], "http://host/2")])
        self.assertEqual(self._kinds(), ["started", "manual"])
        self.assertEqual(self.history.statuses([item["article_url"]])[item["article_url"]], STATUS_MANUAL)

    def test_page_read_error_is_recorded_and_does_not_stop_the_run(self):
        bad, empty = issue("a", "A #1"), issue("b", "B #2")

        with mock.patch.object(service, "resolve_issue_download_links",
                               side_effect=[RuntimeError("boom"), []]):
            result = self._download([bad, empty])

        self.assertEqual(self._kinds(), ["started", "read_failed", "started", "no_links"])
        self.assertEqual(result.total, 2)
        self.assertEqual(len(result.manual_links), 2)
        statuses = self.history.statuses([bad["article_url"], empty["article_url"]])
        self.assertEqual(statuses[bad["article_url"]], STATUS_FAILED)
        self.assertEqual(statuses[empty["article_url"]], STATUS_MANUAL)

    def test_downloader_warnings_become_events(self):
        item = issue("a", "Spider-Man #1 (2020)")

        def fake_download(**kwargs):
            kwargs["log"]("aria2c falhou")
            return False, "http://host/1"

        with mock.patch.object(service, "resolve_issue_download_links",
                               return_value=[{"url": "http://x/1", "label": "A"}]), \
             mock.patch.object(service, "download_single_issue", side_effect=fake_download):
            self._download([item])

        self.assertEqual(self._kinds(), ["started", "warning", "manual"])
        self.assertEqual(self.events[1].detail, "aria2c falhou")


if __name__ == "__main__":
    unittest.main()

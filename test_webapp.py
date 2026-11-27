import unittest
from pathlib import Path
from unittest import mock

import webapp
from jobs import DownloadQueue
from service import ComicService
from webapp import create_app

PACK_URL = "https://getcomics.org/marvel/some-pack/"
ISSUES = [
    {"title": f"Series #{n}", "article_url": f"https://getcomics.org/marvel/series-{n}/"}
    for n in range(1, 4)
]


class FakeService:
    base_dir = Path("/library/Marvel")
    select_issues = staticmethod(ComicService.select_issues)

    def __init__(self):
        self.loaded = []
        self.statuses = {}

    def list_packs(self, page):
        return [{"title": "Pack A", "url": PACK_URL, "meta": "3 edições"}]

    def load_pack(self, pack):
        self.loaded.append(pack["url"])
        return ISSUES

    def issue_statuses(self, pack_title, issues):
        return self.statuses

    def check_library_ready(self):
        return True, ""


class WebAppTests(unittest.TestCase):
    def make(self, password=None):
        self.service = FakeService()
        self.queue = DownloadQueue(self.service, autostart=False)
        app = create_app(self.service, self.queue, password=password, secret_key="test")
        return app.test_client()

    def enqueue(self, client, **data):
        return client.post("/enqueue", data={"url": PACK_URL, "title": "Pack A", **data})

    def pending_titles(self):
        return [[i["title"] for i in j.issues] for j in self.queue.snapshot()["pending"]]

    def test_pages_render(self):
        client = self.make()
        self.assertIn(b"Pack A", client.get("/").data)
        self.assertIn(b"Series #1", client.get("/pack", query_string={"url": PACK_URL, "title": "Pack A"}).data)
        self.assertEqual(client.get("/queue").status_code, 200)

    def test_pack_url_must_be_getcomics(self):
        client = self.make()
        self.assertEqual(client.get("/pack", query_string={"url": "http://evil.example/x"}).status_code, 400)
        self.assertEqual(self.enqueue(client, mode="all").status_code, 302)
        self.assertEqual(
            client.post("/enqueue", data={"url": "http://169.254.169.254/", "mode": "all"}).status_code, 400
        )

    def test_enqueue_modes(self):
        client = self.make()
        self.service.statuses = {ISSUES[0]["article_url"]: "downloaded"}

        self.enqueue(client, mode="pending")
        self.assertEqual(self.pending_titles(), [["Series #2", "Series #3"]])

        self.enqueue(client, mode="selected", i=["0", "99", "x"])
        self.assertEqual(self.pending_titles()[1], ["Series #1"])

        # a mesma escolha outra vez já não acrescenta nada, e não é um erro
        response = self.enqueue(client, mode="all")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.pending_titles()), 2)

    def test_selected_without_selection_adds_nothing(self):
        client = self.make()
        self.enqueue(client, mode="selected")
        self.assertEqual(self.pending_titles(), [])

    def test_pack_is_loaded_once_within_cache_window(self):
        client = self.make()
        client.get("/pack", query_string={"url": PACK_URL, "title": "Pack A"})
        self.enqueue(client, mode="all")
        self.assertEqual(self.service.loaded, [PACK_URL])

    def test_queued_issues_show_in_pack_page(self):
        client = self.make()
        self.enqueue(client, mode="selected", i=["1"])
        page = client.get("/pack", query_string={"url": PACK_URL, "title": "Pack A"}).data.decode()
        self.assertEqual(page.count("na fila"), 1)

    def test_cancel_job(self):
        client = self.make()
        self.enqueue(client, mode="all")
        job_id = self.queue.snapshot()["pending"][0].id
        client.post(f"/queue/cancel/{job_id}")
        self.assertEqual(self.pending_titles(), [])

    def test_login_required_when_password_set(self):
        client = self.make(password="segredo")

        self.assertEqual(client.get("/").headers["Location"], "/login")
        self.assertEqual(client.post("/enqueue", data={"url": PACK_URL, "mode": "all"}).status_code, 302)
        self.assertEqual(self.pending_titles(), [])
        htmx = client.get("/queue/summary", headers={"HX-Request": "true"})
        self.assertEqual((htmx.status_code, htmx.headers["HX-Redirect"]), (401, "/login"))
        client.get("/static/style.css").close()
        self.assertEqual(client.get("/login").status_code, 200)

        with mock.patch.object(webapp.time, "sleep"):
            client.post("/login", data={"password": "errada"})
        self.assertEqual(client.get("/").headers["Location"], "/login")

        client.post("/login", data={"password": "segredo"})
        self.assertEqual(client.get("/").status_code, 200)

        client.post("/logout")
        self.assertEqual(client.get("/").status_code, 302)


if __name__ == "__main__":
    unittest.main()

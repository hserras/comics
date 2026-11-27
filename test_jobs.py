import threading
import unittest

import jobs
from jobs import DownloadQueue
from service import DownloadEvent, DownloadResult


def issue(slug):
    return {"title": f"{slug.upper()} #1", "article_url": f"https://getcomics.org/marvel/{slug}/"}


class FakeService:
    def __init__(self, library_ok=True):
        self.library_ok = library_ok
        self.downloaded = []
        self.during = None  # chamado a meio do download de cada edição
        self.stop_after_first = False

    def check_library_ready(self):
        return (True, "") if self.library_ok else (False, "NAS desligada")

    def download(self, pack_title, issues, on_event=None, should_stop=None):
        result = DownloadResult(succeeded=0, total=len(issues))
        for idx, item in enumerate(issues, 1):
            if should_stop():
                result.cancelled = True
                break
            on_event(DownloadEvent("started", item, idx, len(issues)))
            on_event(DownloadEvent("progress", item, idx, len(issues), bytes_done=50, bytes_total=100))
            if self.during:
                self.during()
            on_event(DownloadEvent("saved", item, idx, len(issues), detail="x.cbz", label="MAIN"))
            self.downloaded.append(item["title"])
            result.succeeded += 1
        return result


class QueueTests(unittest.TestCase):
    def make(self, **kwargs):
        self.service = FakeService(**kwargs)
        return DownloadQueue(self.service, autostart=False)

    def test_second_batch_is_queued_not_refused_and_runs_in_order(self):
        q = self.make()
        first = q.enqueue("Pack 1", [issue("a")])
        second = q.enqueue("Pack 2", [issue("b")])

        self.assertEqual((first.ahead, second.ahead), (0, 1))
        self.assertEqual([j.pack_title for j in q.snapshot()["pending"]], ["Pack 1", "Pack 2"])

        self.assertTrue(q.run_next(block=False))
        self.assertTrue(q.run_next(block=False))
        self.assertFalse(q.run_next(block=False))
        self.assertEqual(self.service.downloaded, ["A #1", "B #1"])
        finished = q.snapshot()["finished"]
        self.assertEqual([j.state for j in finished], [jobs.DONE, jobs.DONE])
        self.assertEqual(finished[0].pack_title, "Pack 2")  # mais recente primeiro

    def test_duplicates_are_skipped(self):
        q = self.make()
        q.enqueue("Pack 1", [issue("a"), issue("b")])
        result = q.enqueue("Pack 2", [issue("b"), issue("c"), issue("c")])

        self.assertEqual(result.skipped, 2)
        self.assertEqual([i["title"] for i in result.job.issues], ["C #1"])
        self.assertIsNone(q.enqueue("Pack 3", [issue("a")]).job)

    def test_states_follow_the_running_job(self):
        q = self.make()
        q.enqueue("Pack 1", [issue("a"), issue("b")])
        q.enqueue("Pack 2", [issue("c")])
        seen = {}
        self.service.during = lambda: seen or seen.update(q.states())
        q.run_next(block=False)

        self.assertEqual(seen["a"], jobs.RUNNING)
        self.assertEqual(seen["b"], jobs.QUEUED)
        self.assertEqual(seen["c"], jobs.QUEUED)
        self.assertEqual(q.states(), {"c": jobs.QUEUED})

    def test_progress_and_log_are_visible_while_running(self):
        q = self.make()
        q.enqueue("Pack 1", [issue("a")])
        seen = {}

        def during():
            seen["job"] = q.snapshot()["running"]

        self.service.during = during
        q.run_next(block=False)

        running = seen["job"]
        self.assertEqual((running.index, running.total, running.percent), (1, 1, 50))
        self.assertEqual(running.current_title, "A #1")

    def test_cancel_pending_job(self):
        q = self.make()
        q.enqueue("Pack 1", [issue("a")])
        second = q.enqueue("Pack 2", [issue("b")])

        self.assertTrue(q.cancel(second.job.id))
        self.assertFalse(q.cancel(second.job.id))
        q.run_next(block=False)
        self.assertFalse(q.run_next(block=False))
        self.assertEqual(self.service.downloaded, ["A #1"])
        self.assertEqual(q.snapshot()["finished"][-1].state, jobs.CANCELLED)

    def test_cancel_running_job_stops_after_current_issue(self):
        q = self.make()
        job = q.enqueue("Pack 1", [issue("a"), issue("b"), issue("c")]).job
        self.service.during = lambda: q.cancel(job.id)
        q.run_next(block=False)

        self.assertEqual(self.service.downloaded, ["A #1"])
        finished = q.snapshot()["finished"][0]
        self.assertEqual((finished.state, finished.succeeded), (jobs.CANCELLED, 1))

    def test_library_not_ready_fails_the_job_and_worker_continues(self):
        q = self.make(library_ok=False)
        q.enqueue("Pack 1", [issue("a")])
        q.run_next(block=False)

        finished = q.snapshot()["finished"][0]
        self.assertEqual((finished.state, finished.error), (jobs.FAILED, "NAS desligada"))
        self.assertIsNone(q.snapshot()["running"])

        self.service.library_ok = True
        q.enqueue("Pack 2", [issue("b")])
        q.run_next(block=False)
        self.assertEqual(self.service.downloaded, ["B #1"])

    def test_worker_thread_processes_jobs(self):
        service = FakeService()
        done = threading.Event()
        service.during = done.set
        q = DownloadQueue(service)
        q.enqueue("Pack 1", [issue("a")])

        self.assertTrue(done.wait(timeout=5))


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_ENV = tempfile.TemporaryDirectory(prefix="nasflow-collections-env-")
os.environ.setdefault("NASFLOW_DATA", str(Path(_ENV.name) / "data"))
os.environ.setdefault("NASFLOW_DOWNLOADS", str(Path(_ENV.name) / "downloads"))

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from server import main
from server.playlists import playlist_entry, speed_bytes, youtube_playlist_url


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nasflow-collection-tests-")
        self.root = Path(self.temp.name)
        self.downloads = self.root / "downloads"
        self.downloads.mkdir()
        self.engine = create_engine(f"sqlite:///{self.root / 'test.db'}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)
        self.patches = [patch.object(main, "engine", self.engine), patch.object(main, "DATA_DIR", self.root),
                        patch.object(main, "DOWNLOAD_DIR", self.downloads), patch.object(main, "dispatch_catalog"),
                        patch.object(main, "dispatch"), patch.object(main, "cookie_file_for_url", return_value=None),
                        patch.object(main, "configured_proxy", return_value=None)]
        self.mock_dispatch = None
        for item in self.patches:
            value = item.start()
            if item.attribute == "dispatch":
                self.mock_dispatch = value
        main.migrate_schema()
        main.login_attempts.clear()
        main.auth.initialize_admin(self.engine, self.root, username="test-admin", password="test-admin-password")
        self.client = TestClient(main.app)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "test-admin", "password": "test-admin-password"}).status_code, 200)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def catalog(self, count=125, title="测试合集"):
        created = self.client.post("/api/collections", json={"url": "https://www.youtube.com/playlist?list=PL_TEST_COLLECTION", "quality": "1080p"})
        self.assertEqual(created.status_code, 201)
        collection_id = created.json()["id"]
        entries = [{"title": title}] + [playlist_entry({"id": f"{index:011d}", "title": f"第 {index} 集"}, index) for index in range(1, count + 1)]
        with patch.object(main, "read_playlist", return_value=iter(entries)):
            main.resolve_collection(collection_id)
        return collection_id

    def entries(self, collection_id):
        with Session(self.engine) as session:
            return list(session.exec(select(main.Task).where(main.Task.collection_id == collection_id).order_by(main.Task.collection_index)).all())

    def test_large_catalog_pagination_duplicate_input_and_private_paths(self):
        collection_id = self.catalog()
        summary = self.client.get("/api/collections").json()[0]
        self.assertEqual(summary["total"], 125)
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["selected"], 0)
        page = self.client.get(f"/api/collections/{collection_id}/entries?page=3").json()
        self.assertEqual(len(page["entries"]), 25)
        self.assertEqual(page["entries"][0]["collection_index"], 101)
        self.assertNotIn(str(self.downloads), str(page))
        self.assertEqual(self.client.get("/api/tasks").json(), [])
        duplicate = self.client.post("/api/collections", json={"url": "https://youtu.be/abcdefghijk?list=PL_TEST_COLLECTION"}).json()
        self.assertEqual(duplicate["id"], collection_id)
        self.assertEqual(len(self.client.get("/api/collections").json()), 1)

    def test_selected_downloads_foreign_ids_and_completed_items_are_not_requeued(self):
        collection_id = self.catalog(5)
        entries = self.entries(collection_id)
        response = self.client.post(f"/api/collections/{collection_id}/start", json={"task_ids": [entries[0].id, entries[2].id]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["queued"], 2)
        self.assertEqual(response.json()["pending"], 3)
        self.assertEqual(self.mock_dispatch.call_count, 2)
        self.assertEqual(self.client.post(f"/api/collections/{collection_id}/start", json={"task_ids": ["another-collection-id"]}).status_code, 422)
        main.update_task(entries[0].id, status="completed", progress=100)
        self.client.post(f"/api/collections/{collection_id}/start", json={"task_ids": [entries[0].id, entries[2].id]})
        self.assertEqual(self.mock_dispatch.call_count, 2)
        self.assertEqual(self.client.get("/api/collections").json()[0]["progress"], 50)

    def test_start_and_resume_dispatch_scalar_ids_to_the_real_download_worker(self):
        collection_id = self.catalog(3)
        entries = self.entries(collection_id)
        self.client.post(f"/api/collections/{collection_id}/start", json={"task_ids": [entries[1].id]})
        self.client.post(f"/api/collections/{collection_id}/pause")
        def run(task_id):
            self.assertIsInstance(task_id, str)
            main.run_download(task_id)
        self.mock_dispatch.side_effect = run
        process = MagicMock()
        process.stdout = ["__NASFLOW_TITLE__Actual worker title"]
        process.wait.return_value = 0
        with patch.object(main.subprocess, "Popen", return_value=process):
            response = self.client.post(f"/api/collections/{collection_id}/start", json={"task_ids": [entries[0].id]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["completed"], 1)
            resumed = self.client.post(f"/api/collections/{collection_id}/resume")
            self.assertEqual(resumed.status_code, 200)
            self.assertEqual(resumed.json()["completed"], 2)
        self.assertEqual([entry.status for entry in self.entries(collection_id)], ["completed", "completed", "pending"])

    def test_cancel_finds_the_running_process_by_scalar_id(self):
        collection_id = self.catalog(2)
        entries = self.entries(collection_id)
        main.update_task(entries[0].id, status="running")
        process = MagicMock()
        with patch.dict(main.processes, {entries[0].id: process}):
            response = self.client.post(f"/api/collections/{collection_id}/cancel")
            self.assertEqual(response.status_code, 200)
        process.terminate.assert_called_once()

    def test_pause_resume_cancel_retry_and_library_filter(self):
        collection_id = self.catalog(5)
        entries = self.entries(collection_id)
        self.client.post(f"/api/collections/{collection_id}/start", json={})
        main.update_task(entries[0].id, status="completed", progress=100)
        main.update_task(entries[1].id, status="running", speed="2.5MiB/s")
        main.update_task(entries[2].id, status="failed")
        paused = self.client.post(f"/api/collections/{collection_id}/pause").json()
        self.assertEqual(paused["paused"], 2)
        self.assertEqual(paused["running"], 1)
        self.assertEqual(paused["speed_bytes"], 2.5 * 1024 ** 2)
        self.assertEqual(paused["status"], "paused")
        main.run_download(entries[3].id)
        self.assertEqual(self.entries(collection_id)[3].status, "paused")
        resumed = self.client.post(f"/api/collections/{collection_id}/resume").json()
        self.assertEqual(resumed["queued"], 2)
        retried = self.client.post(f"/api/collections/{collection_id}/retry").json()
        self.assertEqual(retried["failed"], 0)
        self.assertEqual(retried["queued"], 3)
        canceled = self.client.post(f"/api/collections/{collection_id}/cancel").json()
        self.assertEqual(canceled["completed"], 1)
        self.assertEqual(canceled["cancelled"], 4)
        page = self.client.get(f"/api/collections/{collection_id}/entries?view=library&status=completed").json()
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["entries"][0]["id"], entries[0].id)

    def test_cancel_and_continue_preserve_unselected_episodes(self):
        collection_id = self.catalog(5)
        entries = self.entries(collection_id)
        self.client.post(f"/api/collections/{collection_id}/start", json={"task_ids": [entries[0].id, entries[2].id]})
        canceled = self.client.post(f"/api/collections/{collection_id}/cancel").json()
        self.assertEqual(canceled["cancelled"], 2)
        self.assertEqual(canceled["pending"], 3)
        resumed = self.client.post(f"/api/collections/{collection_id}/resume").json()
        self.assertEqual(resumed["queued"], 2)
        self.assertEqual(resumed["pending"], 3)

    def test_catalog_rescan_keeps_files_directory_and_skips_existing_episodes(self):
        collection_id = self.catalog(3, "../../原始合集")
        original = self.entries(collection_id)
        main.update_task(original[0].id, status="completed")
        with Session(self.engine) as session:
            folder = session.get(main.Collection, collection_id).folder_name
        self.client.post(f"/api/collections/{collection_id}/rescan")
        entries = [{"title": "合集已改名"}] + [playlist_entry({"id": f"{index:011d}", "title": f"第 {index} 集"}, index) for index in range(1, 5)]
        entries.append(entries[-1])
        with patch.object(main, "read_playlist", return_value=iter(entries)):
            main.resolve_collection(collection_id)
        self.assertEqual(len(self.entries(collection_id)), 4)
        self.assertEqual(self.entries(collection_id)[0].status, "completed")
        with Session(self.engine) as session:
            self.assertEqual(session.get(main.Collection, collection_id).folder_name, folder)
        command, target = main.build_command(self.entries(collection_id)[-1])
        self.assertTrue(target.is_relative_to(self.downloads))
        self.assertEqual(target.name, folder)
        self.assertIn("--no-playlist", command)
        self.assertIn("--merge-output-format", command)
        self.assertIn("avc1", command[command.index("-f") + 1])
        self.assertTrue(command[command.index("-o") + 1].endswith("0004 - %(title)s [%(id)s].%(ext)s"))

    def test_catalog_error_and_cancel_do_not_start_downloads(self):
        created = self.client.post("/api/collections", json={"url": "https://youtube.com/playlist?list=PL_TEST_COLLECTION"}).json()
        with patch.object(main, "read_playlist", side_effect=RuntimeError("network timeout /private/path")):
            main.resolve_collection(created["id"])
        summary = self.client.get("/api/collections").json()[0]
        self.assertEqual(summary["status"], "failed")
        self.assertNotIn("/private/path", summary["error"])
        self.assertEqual(self.client.post(f"/api/collections/{created['id']}/start", json={}).status_code, 409)
        self.client.post(f"/api/collections/{created['id']}/cancel")
        with patch.object(main, "read_playlist") as reader:
            main.resolve_collection(created["id"])
            reader.assert_not_called()
        self.mock_dispatch.assert_not_called()

    def test_collection_api_requires_admin_and_accepts_only_youtube_playlists(self):
        with TestClient(main.app) as guest:
            self.assertEqual(guest.get("/api/collections").status_code, 401)
            self.assertEqual(guest.post("/api/collections", json={"url": "https://youtube.com/playlist?list=PL_TEST_COLLECTION"}).status_code, 401)
        self.assertEqual(self.client.post("/api/collections", json={"url": "https://youtube.com.attacker.test/?list=PL_TEST_COLLECTION"}).status_code, 422)
        self.assertEqual(self.client.post("/api/collections", json={"url": "https://youtube.com/watch?v=abcdefghijk"}).status_code, 422)
        self.assertEqual(youtube_playlist_url("https://youtube.com/watch?v=abcdefghijk&list=PL_TEST_COLLECTION&index=3"), "https://www.youtube.com/playlist?list=PL_TEST_COLLECTION")
        self.assertEqual(playlist_entry({"id": "abcdefghijk", "url": "https://attacker.test/file"}, 1)["url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(speed_bytes("8.5 MB/s"), 8_500_000)

    def test_existing_database_migration_preserves_download_records(self):
        legacy = create_engine(f"sqlite:///{self.root / 'legacy.db'}")
        with legacy.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE task (id TEXT PRIMARY KEY, url TEXT, title TEXT, engine TEXT, status TEXT, progress FLOAT, speed TEXT, eta TEXT, error TEXT, output_path TEXT, created_at DATETIME, updated_at DATETIME)")
            connection.exec_driver_sql("INSERT INTO task (id,url,title,status) VALUES ('old', 'https://youtu.be/abcdefghijk', '旧视频', 'completed')")
            connection.exec_driver_sql("CREATE TABLE mediashare (id TEXT PRIMARY KEY)")
        with patch.object(main, "engine", legacy):
            main.migrate_schema()
            SQLModel.metadata.create_all(legacy)
            with Session(legacy) as session:
                task = session.get(main.Task, "old")
                self.assertEqual(task.title, "旧视频")
                self.assertEqual(task.status, "completed")
                self.assertIsNone(task.collection_id)
        legacy.dispose()

    def test_download_completion_does_not_overwrite_cancelled_or_paused_tasks(self):
        collection_id = self.catalog(3)
        entries = self.entries(collection_id)
        main.update_task(entries[0].id, status="cancelled")
        main.update_task(entries[1].id, status="paused")
        main.update_task(entries[2].id, status="running")
        self.assertFalse(main.finish_running_task(entries[0].id, status="completed", progress=100))
        self.assertFalse(main.finish_running_task(entries[1].id, status="failed"))
        self.assertTrue(main.finish_running_task(entries[2].id, status="completed", progress=100))
        self.assertEqual([entry.status for entry in self.entries(collection_id)], ["cancelled", "paused", "completed"])


if __name__ == "__main__":
    unittest.main()

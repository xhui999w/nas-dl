import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_ENV = tempfile.TemporaryDirectory(prefix="nasflow-media-env-")
os.environ.setdefault("NASFLOW_DATA", str(Path(_ENV.name) / "data"))
os.environ.setdefault("NASFLOW_DOWNLOADS", str(Path(_ENV.name) / "downloads"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402
from server import main  # noqa: E402
from server.media import UNSUPPORTED_MESSAGE, video_metadata  # noqa: E402


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nasflow-media-tests-")
        self.root = Path(self.temp.name) / "downloads"
        self.root.mkdir()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'test.db'}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)
        self.patches = [patch.object(main, "DOWNLOAD_DIR", self.root), patch.object(main, "engine", self.engine),
                        patch("server.media._probe_codecs", return_value=(("h264",), ("aac",)))]
        for item in self.patches:
            item.start()
        self.client = TestClient(main.app)
        self.content = bytes(range(256)) * 1024
        self.video = self.root / "测试视频.mp4"
        self.video.write_bytes(self.content)
        self.id = self.add_task(self.video)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def add_task(self, path, status="completed"):
        task = main.Task(url="https://www.youtube.com/watch?v=test", title="测试视频名称", status=status, output_path=str(path))
        with Session(self.engine) as session:
            session.add(task)
            session.commit()
            session.refresh(task)
            return task.id

    def test_metadata_and_task_list_do_not_expose_file_paths(self):
        response = self.client.get(f"/api/media/{self.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "测试视频名称")
        self.assertTrue(response.json()["supported"])
        self.assertNotIn(str(self.root), response.text)
        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()[0]["media_available"])
        self.assertEqual(response.json()[0]["output_path"], self.video.name)
        self.assertNotIn(str(self.root), response.text)

    def test_exact_middle_range_and_headers(self):
        response = self.client.get(f"/api/media/{self.id}/stream", headers={"Range": "bytes=100000-100099"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, self.content[100000:100100])
        self.assertEqual(response.headers["content-range"], f"bytes 100000-100099/{len(self.content)}")
        self.assertEqual(response.headers["content-length"], "100")
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertTrue(response.headers["content-disposition"].startswith("inline"))

    def test_suffix_open_range_head_and_if_range(self):
        url = f"/api/media/{self.id}/stream"
        self.assertEqual(self.client.get(url, headers={"Range": "bytes=-32"}).content, self.content[-32:])
        self.assertEqual(self.client.get(url, headers={"Range": "bytes=260000-"}).content, self.content[260000:])
        head = self.client.head(url)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(int(head.headers["content-length"]), len(self.content))
        partial = self.client.get(url, headers={"Range": "bytes=10-19", "If-Range": head.headers["etag"]})
        self.assertEqual(partial.status_code, 206)
        full = self.client.get(url, headers={"Range": "bytes=10-19", "If-Range": '"old-file"'})
        self.assertEqual(full.status_code, 200)

    def test_invalid_and_out_of_bounds_ranges(self):
        url = f"/api/media/{self.id}/stream"
        response = self.client.get(url, headers={"Range": f"bytes={len(self.content)}-"})
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response.headers["content-range"], f"*/{len(self.content)}")
        self.assertEqual(self.client.get(url, headers={"Range": "words=0-10"}).status_code, 400)

    def test_missing_unfinished_directory_and_non_video_records(self):
        for path, status, expected in [(self.root / "missing.mp4", "completed", 404),
                                       (self.video, "running", 409), (self.root, "completed", 404)]:
            task_id = self.add_task(path, status)
            self.assertEqual(self.client.get(f"/api/media/{task_id}").status_code, expected)
            self.assertEqual(self.client.get(f"/api/media/{task_id}/stream").status_code, expected)
            self.assertFalse(self.client.get(f"/api/tasks/{task_id}").json()["media_available"])
        image = self.root / "photo.jpg"
        image.write_bytes(b"image")
        task_id = self.add_task(image)
        self.assertFalse(self.client.get(f"/api/tasks/{task_id}").json()["media_available"])
        self.assertEqual(self.client.get(f"/api/media/{task_id}").status_code, 415)
        self.assertEqual(self.client.get("/api/media/not-a-record/stream").status_code, 404)

    def test_traversal_and_outside_symlink_are_blocked(self):
        outside = self.root.parent / "private.mp4"
        outside.write_bytes(b"private")
        for path in [outside, self.root / ".." / "private.mp4"]:
            task_id = self.add_task(path)
            self.assertEqual(self.client.get(f"/api/media/{task_id}/stream").status_code, 403)
            self.assertFalse(self.client.get(f"/api/tasks/{task_id}").json()["media_available"])
        link = self.root / "symlink.mp4"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("OS does not allow this account to create symlinks")
        task_id = self.add_task(link)
        self.assertEqual(self.client.get(f"/api/media/{task_id}/stream").status_code, 403)

    def test_url_cannot_override_database_path_and_download_still_works(self):
        response = self.client.get(f"/api/media/{self.id}/stream?path=/etc/passwd", headers={"Range": "bytes=0-15"})
        self.assertEqual(response.content, self.content[:16])
        response = self.client.get(f"/api/tasks/{self.id}/file")
        self.assertEqual(response.content, self.content)
        self.assertTrue(response.headers["content-disposition"].startswith("attachment"))

    def test_unsupported_containers_and_codecs(self):
        mkv = self.root / "video.mkv"
        mkv.write_bytes(b"video")
        task_id = self.add_task(mkv)
        self.assertTrue(self.client.get(f"/api/tasks/{task_id}").json()["media_available"])
        self.assertEqual(self.client.get(f"/api/media/{task_id}").json()["message"], UNSUPPORTED_MESSAGE)
        self.assertEqual(self.client.get(f"/api/media/{task_id}/stream").status_code, 415)
        for codecs in [(("hevc",), ("aac",)), (("h264",), ("dts",)), ((), ("aac",))]:
            with patch("server.media._probe_codecs", return_value=codecs):
                self.assertFalse(video_metadata(self.video)["supported"])
        for extension, codecs in [("webm", (("vp9",), ("opus",))), ("ogv", (("theora",), ("vorbis",)))]:
            with patch("server.media._probe_codecs", return_value=codecs):
                file = self.root / f"video.{extension}"
                file.write_bytes(b"video")
                self.assertTrue(video_metadata(file)["supported"])


if __name__ == "__main__":
    unittest.main()

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
        self.patches = [patch.object(main, "DOWNLOAD_DIR", self.root), patch.object(main, "DATA_DIR", Path(self.temp.name)), patch.object(main, "engine", self.engine),
                        patch("server.media._probe_codecs", return_value=(("h264",), ("aac",)))]
        for item in self.patches:
            item.start()
        self.client = TestClient(main.app)
        main.login_attempts.clear()
        main.auth.initialize_admin(self.engine, Path(self.temp.name), username="test-admin", password="test-admin-password")
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "test-admin", "password": "test-admin-password"}).status_code, 200)
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

    def test_share_stream_counts_range_bytes_and_stops_at_quota(self):
        client = TestClient(main.app, base_url="https://testserver")
        try:
            created = self.client.post(f"/api/tasks/{self.id}/shares", json={"plays": 1})
            self.assertEqual(created.status_code, 201)
            token = created.json()["token"]
            with Session(self.engine) as session:
                share = session.get(main.MediaShare, created.json()["id"])
                share.byte_limit = 200
                session.add(share)
                session.commit()

            started = client.post(f"/api/shares/{token}/play")
            self.assertEqual(started.status_code, 200)
            client.cookies.set(f"nasflow_share_{token[:12]}", started.cookies.get(f"nasflow_share_{token[:12]}"),
                               path=f"/api/shares/{token}/")
            stream = f"/api/shares/{token}/stream"
            self.assertEqual(client.head(stream).status_code, 200)
            self.assertEqual(client.get(f"/api/shares/{token}").json()["transferred_bytes"], 0)
            first = client.get(stream, headers={"Range": "bytes=10-109"})
            self.assertEqual(first.status_code, 206)
            self.assertEqual(len(first.content), 100)
            second = client.get(stream, headers={"Range": "bytes=200-299"})
            self.assertEqual(second.status_code, 206)
            self.assertEqual(len(second.content), 100)
            self.assertEqual(client.get(stream, headers={"Range": "bytes=300-399"}).status_code, 410)

            status = client.get(f"/api/shares/{token}").json()
            self.assertEqual(status["transferred_bytes"], 200)
            self.assertEqual(status["remaining_bytes"], 0)
        finally:
            client.close()

    def test_guest_cannot_read_or_change_management_data(self):
        with TestClient(main.app) as guest:
            for path in ["/api/tasks", "/api/cookies", "/api/settings", "/api/subscriptions",
                         f"/api/tasks/{self.id}/file", f"/api/media/{self.id}", f"/api/media/{self.id}/stream"]:
                self.assertEqual(guest.get(path).status_code, 401, path)
            self.assertEqual(guest.post("/api/tasks", json={"url": "https://example.com/video"}).status_code, 401)
            created = self.client.post(f"/api/tasks/{self.id}/shares", json={"plays": 1}).json()
            self.assertEqual(guest.get(f"/api/shares/{created['token']}").status_code, 200)
            self.assertNotIn("id", guest.get(f"/api/shares/{created['token']}").json())
            self.assertEqual(guest.delete(f"/api/shares/{created['id']}").status_code, 401)
            self.assertEqual(guest.get(f"/api/tasks/{self.id}/shares").status_code, 401)

    def test_logout_revokes_saved_cookie_and_session_tokens_are_hashed(self):
        token = self.client.cookies.get(main.auth.COOKIE_NAME)
        with Session(self.engine) as session:
            saved = session.get(main.auth.AdminSession, main.auth.token_hash(token))
            self.assertIsNotNone(saved)
            self.assertNotEqual(saved.token_hash, token)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.client.cookies.set(main.auth.COOKIE_NAME, token)
        self.assertEqual(self.client.get("/api/tasks").status_code, 401)

    def test_external_player_token_is_scoped_and_revoked_on_logout(self):
        with TestClient(main.app) as guest:
            self.assertEqual(guest.post(f"/api/media/{self.id}/external-token").status_code, 401)
            token = self.client.post(f"/api/media/{self.id}/external-token").json()["token"]
            url = f"/api/media/{self.id}/external-stream?token={token}"
            response = guest.get(url, headers={"Range": "bytes=10-19"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, self.content[10:20])
            other = self.add_task(self.video)
            self.assertEqual(guest.get(f"/api/media/{other}/external-stream?token={token}").status_code, 403)
            self.assertEqual(guest.get(f"/api/media/{self.id}/external-stream").status_code, 403)
            self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
            self.assertEqual(guest.get(url).status_code, 403)

    def test_password_change_requires_old_password_and_revokes_all_logins(self):
        token = self.client.cookies.get(main.auth.COOKIE_NAME)
        payload = {"username": "new-admin", "current_password": "wrong", "new_password": "new-admin-password"}
        self.assertEqual(self.client.post("/api/auth/credentials", json=payload).status_code, 401)
        payload["current_password"] = "test-admin-password"
        self.assertEqual(self.client.post("/api/auth/credentials", json=payload).status_code, 200)
        self.client.cookies.set(main.auth.COOKIE_NAME, token)
        self.assertEqual(self.client.get("/api/auth/session").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "test-admin", "password": "test-admin-password"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "new-admin", "password": "new-admin-password"}).status_code, 200)

    def test_cross_origin_mutation_is_blocked_and_https_cookies_are_secure(self):
        self.assertEqual(self.client.post(f"/api/tasks/{self.id}/cancel", headers={"Origin": "https://untrusted.example"}).status_code, 403)
        response = self.client.post("/api/auth/login", json={"username": "test-admin", "password": "test-admin-password"},
                                    headers={"Origin": "https://nas.example", "X-Forwarded-Host": "nas.example", "X-Forwarded-Proto": "https"})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("secure", cookie)
        self.assertIn("samesite=lax", cookie)

    def test_tampered_and_expired_admin_sessions_are_rejected(self):
        token = self.client.cookies.get(main.auth.COOKIE_NAME)
        self.client.cookies.clear()
        self.client.cookies.set(main.auth.COOKIE_NAME, "tampered")
        self.assertEqual(self.client.get("/api/tasks").status_code, 401)
        with Session(self.engine) as session:
            saved = session.get(main.auth.AdminSession, main.auth.token_hash(token))
            saved.expires_at = 0
            session.add(saved)
            session.commit()
        self.client.cookies.set(main.auth.COOKIE_NAME, token)
        self.assertEqual(self.client.get("/api/tasks").status_code, 401)

    def test_login_attempts_are_limited(self):
        main.login_attempts.clear()
        for _ in range(10):
            response = self.client.post("/api/auth/login", json={"username": "test-admin", "password": "wrong"})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "test-admin", "password": "wrong"}).status_code, 429)

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

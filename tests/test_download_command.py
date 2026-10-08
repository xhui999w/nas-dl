import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix="nasflow-command-tests-")
os.environ["NASFLOW_DATA"] = str(Path(_TEMP.name) / "data")
os.environ["NASFLOW_DOWNLOADS"] = str(Path(_TEMP.name) / "downloads")

from server.main import Task, Setting, build_command, choose_engine, parse_progress, cookie_file_for_url  # noqa: E402


class DownloadCommandTests(unittest.TestCase):
    def test_new_attempt_classifies_only_new_download_output(self) -> None:
        from sqlmodel import SQLModel, Session, create_engine
        from unittest.mock import MagicMock
        from server import main
        database = create_engine("sqlite://")
        SQLModel.metadata.create_all(database)
        task = Task(url="https://www.douyin.com/video/7443830253836963126", status="queued",
                    log_tail="ERROR: Unsupported URL from previous attempt")
        with Session(database) as session:
            session.add(task)
            session.commit()
            task_id = task.id
        process = MagicMock()
        process.stdout = iter(["ERROR: Fresh cookies are needed\n"])
        process.wait.return_value = 1
        with patch.object(main, "engine", database), patch.object(main, "build_command", return_value=(["fake"], Path(_TEMP.name))), patch.object(main.subprocess, "Popen", return_value=process):
            main.run_download(task_id)
        with Session(database) as session:
            result = session.get(Task, task_id)
            self.assertEqual(result.error_type, "COOKIE_REQUIRED")
            self.assertNotIn("previous attempt", result.log_tail)
        database.dispose()

    def test_share_host_uses_existing_canonical_cookie_rule(self) -> None:
        setting = Setting(key="cookies", value=json.dumps({"rules": [{"domain": "douyin.com", "cookie": "ttwid=test"}]}))
        with tempfile.TemporaryDirectory() as folder, patch("server.main.DATA_DIR", Path(folder)), patch("server.main.Session") as session:
            session.return_value.__enter__.return_value.get.return_value = setting
            cookie_file = cookie_file_for_url("https://www.iesdouyin.com/share/note/7443830253836963126/")
            self.assertIsNotNone(cookie_file)
            self.assertIn(".douyin.com", cookie_file.read_text(encoding="utf-8"))

    def test_douyin_note_routes_to_image_adapter_with_existing_proxy_and_cookies(self) -> None:
        task = Task(url="https://www.iesdouyin.com/share/note/7443830253836963126/", engine="yt-dlp")
        with patch("server.main.cookie_file_for_url", return_value=Path("cookies.txt")), patch("server.main.configured_proxy", return_value="http://proxy:7890"):
            command, _ = build_command(task)
        self.assertEqual(command[2], "server.douyin_notes")
        self.assertIn("cookies.txt", command)
        self.assertIn("http://proxy:7890", command)

    def test_ffmpeg_metadata_flag_does_not_break_print_arguments(self) -> None:
        url = "https://youtube.com/shorts/tn40jotIp6o?is=test"
        task = Task(url=url, engine="yt-dlp", quality="best", folder="自动分类")
        with (
            patch("server.main.cookie_file_for_url", return_value=None),
            patch("server.main.configured_proxy", return_value=None),
            patch("server.main.shutil.which", return_value="/usr/bin/ffmpeg"),
        ):
            command, _ = build_command(task)
        print_positions = [index for index, value in enumerate(command) if value == "--print"]
        self.assertEqual(len(print_positions), 2)
        self.assertTrue(command[print_positions[0] + 1].startswith("before_dl:"))
        self.assertTrue(command[print_positions[1] + 1].startswith("after_move:"))
        self.assertIn("--progress", command)
        self.assertEqual(command[-2:], ["--embed-metadata", url])

    def test_parses_yt_dlp_speed_and_eta(self) -> None:
        percent, speed, eta = parse_progress("[download]  42.5% of 100.00MiB at 8.75MiB/s ETA 00:07")
        self.assertEqual(percent, 42.5)
        self.assertEqual(speed, "8.75MiB/s")
        self.assertEqual(eta, "00:07")

    def test_video_post_routes_to_yt_dlp(self) -> None:
        self.assertEqual(choose_engine("https://www.instagram.com/reel/abc/", "auto"), "yt-dlp")
        self.assertEqual(choose_engine("https://x.com/example/status/123", "auto"), "yt-dlp")
        self.assertEqual(choose_engine("https://www.instagram.com/example/", "auto"), "gallery-dl")


if __name__ == "__main__":
    unittest.main()

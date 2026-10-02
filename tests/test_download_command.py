import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory(prefix="nasflow-command-tests-")
os.environ["NASFLOW_DATA"] = str(Path(_TEMP.name) / "data")
os.environ["NASFLOW_DOWNLOADS"] = str(Path(_TEMP.name) / "downloads")

from server.main import Task, build_command, choose_engine, parse_progress  # noqa: E402


class DownloadCommandTests(unittest.TestCase):
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

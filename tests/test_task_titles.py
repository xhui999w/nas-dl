import json
import os
import tempfile
import unittest
from pathlib import Path

_TEMP = tempfile.TemporaryDirectory(prefix="nasflow-title-tests-")
os.environ["NASFLOW_DATA"] = str(Path(_TEMP.name) / "data")
os.environ["NASFLOW_DOWNLOADS"] = str(Path(_TEMP.name) / "downloads")

from server.main import (  # noqa: E402
    Task,
    build_info_title_indexes,
    recover_task_title,
)


class TaskTitleTests(unittest.TestCase):
    def test_recovers_title_from_info_json_for_legacy_task(self) -> None:
        download_dir = Path(os.environ["NASFLOW_DOWNLOADS"])
        output_path = download_dir / "自动分类" / "作者" / "视频 [abc123].mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.touch()
        Path(f"{output_path}.info.json").write_text(
            json.dumps({
                "title": "真实视频标题",
                "original_url": "https://example.com/video/abc123?share=1",
                "webpage_url": "https://example.com/video/abc123",
            }),
            encoding="utf-8",
        )

        exact, by_path = build_info_title_indexes()
        task = Task(
            url="https://example.com/video/abc123?share=2",
            title="等待解析",
            output_path=str(output_path),
        )
        self.assertEqual(recover_task_title(task, exact, by_path), "真实视频标题")

    def test_falls_back_to_download_filename(self) -> None:
        download_dir = Path(os.environ["NASFLOW_DOWNLOADS"])
        output_path = download_dir / "自动分类" / "作者" / "文件标题 [xyz].mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.touch()

        task = Task(url="https://example.com/video/xyz", title="等待解析", output_path=str(output_path))
        self.assertEqual(recover_task_title(task, {}, {}), "文件标题")


if __name__ == "__main__":
    unittest.main()

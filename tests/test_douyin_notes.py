import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from server.douyin_notes import cookie_header, download_note, fetch_image, note_id, read_note, safe_name

NOTE = "https://www.iesdouyin.com/share/note/7443830253836963126/"


class DouyinNoteTests(unittest.TestCase):
    def test_note_matches_only_official_post_urls(self):
        for url in (NOTE, "https://www.douyin.com/note/7443830253836963126?share=1"):
            self.assertEqual(note_id(url), "7443830253836963126")
        for url in ("https://evil.douyin.com/share/note/7443830253836963126/",
                    "https://douyin.com.evil.test/note/7443830253836963126",
                    "https://www.douyin.com/video/7443830253836963126", "file:///note/7443830253836963126"):
            self.assertIsNone(note_id(url))

    def test_cookie_scope_and_official_request_do_not_follow_redirects(self):
        with tempfile.TemporaryDirectory() as folder:
            cookie_file = Path(folder) / "cookies.txt"
            cookie_file.write_text("# Netscape HTTP Cookie File\n.douyin.com\tTRUE\t/\tTRUE\t2147483647\tallowed\ttest\n.example.com\tTRUE\t/\tTRUE\t2147483647\tunrelated\tprivate\n")
            self.assertEqual(cookie_header(str(cookie_file)), "allowed=test")
            with patch("server.douyin_notes.requests.get") as get:
                response = get.return_value
                response.status_code = 200
                response.content = b"{}"
                response.json.return_value = {"aweme_detail": {"aweme_id": "7443830253836963126"}}
                read_note("7443830253836963126", str(cookie_file), "http://proxy:7890")
                self.assertFalse(get.call_args.kwargs["allow_redirects"])
                self.assertEqual(get.call_args.kwargs["headers"]["Cookie"], "allowed=test")
                self.assertEqual(get.call_args.kwargs["params"]["aid"], "6383")

    def test_cdn_fetch_sends_no_login_cookie_and_rejects_fake_image(self):
        with patch("server.douyin_notes.requests.get") as get:
            response = get.return_value
            response.status_code = 200
            response.iter_content.return_value = iter([b"<html>error</html>"])
            with self.assertRaises(ValueError):
                fetch_image(["https://p3-pc-sign.douyinpic.com/a.jpeg"], None)
            self.assertNotIn("Cookie", get.call_args.kwargs["headers"])
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
            get.reset_mock()
            with self.assertRaises(ValueError):
                fetch_image(["http://127.0.0.1/private", "https://douyinpic.com.evil.test/a.jpg"], None)
            get.assert_not_called()

    def test_pack_contains_every_picture_in_original_order_and_title(self):
        detail = {"aweme_id": "7443830253836963126", "desc": "枫叶/作品", "author": {"nickname": "../作者"},
                  "images": [{"url_list": ["https://p3-pc-sign.douyinpic.com/1"]}, {"url_list": ["https://p3-pc-sign.douyinpic.com/2"]}]}
        with tempfile.TemporaryDirectory() as folder, patch("server.douyin_notes.read_note", return_value=detail), \
                patch("server.douyin_notes.fetch_image", side_effect=[(b"first", "jpg"), (b"second", "png")]):
            destination = download_note(NOTE, Path(folder), None, None)
            self.assertTrue(destination.resolve().is_relative_to(Path(folder).resolve()))
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(archive.namelist(), ["001.jpg", "002.png", "作品信息.json"])
                self.assertEqual(archive.read("001.jpg"), b"first")
                self.assertEqual(archive.read("002.png"), b"second")
            info = json.loads(destination.with_suffix(".info.json").read_text(encoding="utf-8"))
            self.assertEqual(info["title"], "枫叶/作品")
            self.assertFalse(destination.with_suffix(".zip.part").exists())

    def test_failed_picture_does_not_publish_incomplete_zip(self):
        detail = {"aweme_id": "7443830253836963126", "images": [{"url_list": []}]}
        with tempfile.TemporaryDirectory() as folder, patch("server.douyin_notes.read_note", return_value=detail), \
                patch("server.douyin_notes.fetch_image", side_effect=ValueError("expired")):
            with self.assertRaises(ValueError):
                download_note(NOTE, Path(folder), None, None)
            self.assertEqual(list(Path(folder).rglob("*.zip")), [])
            self.assertEqual(list(Path(folder).rglob("*.part")), [])

    def test_chinese_filename_fits_filesystem_byte_limit(self):
        self.assertLessEqual(len(safe_name("枫" * 200).encode("utf-8")), 160)


if __name__ == "__main__":
    unittest.main()

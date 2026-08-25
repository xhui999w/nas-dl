import unittest

from server.download_errors import classify_download_error


class DownloadErrorClassificationTests(unittest.TestCase):
    def test_known_error_classes(self) -> None:
        cases = {
            "Fresh cookies (not necessarily logged in) are needed": "COOKIE_REQUIRED",
            "This video is DRM protected": "DRM",
            "This video is not available in your country": "REGION_LOCKED",
            "HTTP Error 429: Too Many Requests": "BLOCKED",
            "Your IP address is blocked from accessing this post": "BLOCKED",
            "Network is unreachable": "NETWORK_ERROR",
            "ERROR: Unsupported URL": "UNSUPPORTED",
            "No video formats found": "PARSE_ERROR",
            "Fixed output name but more than one file to download": "DOWNLOADER_ERROR",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(classify_download_error(message), expected)


if __name__ == "__main__":
    unittest.main()

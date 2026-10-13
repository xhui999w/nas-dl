import unittest
from http.cookies import SimpleCookie
from unittest.mock import patch

from server.douyin_signing import form_query, signed_detail_query, SIGNATURE_KEY
from server.plugins.nasflow.yt_dlp_plugins.extractor.douyin import _SignedDouyinIE, _UpstreamDouyinIE


class DouyinSigningTests(unittest.TestCase):
    def test_checksum_matches_independent_browser_urlsearchparams_fixture(self):
        # Fixture calculated independently with Node URLSearchParams + crypto.
        result = signed_detail_query({"aweme_id": "7670754081590360226", "aid": "6383"}, "test UIFID+/~*")
        self.assertEqual(result[SIGNATURE_KEY], "c6dea248d502837e346da7d78740fc0b")
        self.assertEqual(form_query({k: v for k, v in result.items() if k != SIGNATURE_KEY}),
                         "aweme_id=7670754081590360226&aid=6383&uifid=test+UIFID%2B%2F%7E*")
        self.assertEqual(form_query({"description": "空间 ~*+&/"}), "description=%E7%A9%BA%E9%97%B4+%7E*%2B%26%2F")

    def test_stale_signature_is_replaced_without_mutating_input(self):
        query = {"aweme_id": "7670754081590360226", SIGNATURE_KEY: "stale"}
        result = signed_detail_query(query, "test-request-id")
        self.assertNotEqual(result[SIGNATURE_KEY], "stale")
        self.assertEqual(query[SIGNATURE_KEY], "stale")
        self.assertEqual(len(result[SIGNATURE_KEY]), 32)
        for invalid in ("", "request\r\nheader", "x" * 8193):
            with self.assertRaises(ValueError): signed_detail_query(query, invalid)

    def test_plugin_signs_only_fixed_official_detail_endpoint(self):
        extractor = _SignedDouyinIE()
        cookie = SimpleCookie("UIFID=test-request-id")
        with patch.object(extractor, "_get_cookies", return_value=cookie), \
                patch.object(_UpstreamDouyinIE, "_download_json", return_value={"aweme_detail": {}}) as native:
            extractor._download_json("https://www.douyin.com/aweme/v1/web/aweme/detail/", "7670754081590360226",
                                     query={"aweme_id": "7670754081590360226"}, fatal=False)
            self.assertIn(SIGNATURE_KEY, native.call_args.kwargs["query"])
            self.assertFalse(native.call_args.kwargs["fatal"])
        with patch.object(extractor, "_get_cookies") as cookies, patch.object(_UpstreamDouyinIE, "_download_json") as native:
            url = "https://www.douyin.com.evil.test/aweme/v1/web/aweme/detail/"
            extractor._download_json(url, "7670754081590360226", query={"aweme_id": "7670754081590360226"})
            cookies.assert_not_called()
            self.assertNotIn(SIGNATURE_KEY, native.call_args.kwargs["query"])

    def test_plugin_can_use_anonymous_identifier_from_official_homepage(self):
        extractor = _SignedDouyinIE()
        with patch.object(extractor, "_get_cookies", side_effect=[{}, SimpleCookie("UIFID_TEMP=test-anonymous-id")]), \
                patch.object(extractor, "_download_webpage") as homepage, patch.object(_UpstreamDouyinIE, "_download_json") as native:
            extractor._download_json("https://www.douyin.com/aweme/v1/web/aweme/detail/", "7670754081590360226",
                                     query={"aweme_id": "7670754081590360226"})
            self.assertEqual(homepage.call_args.args[0], "https://www.douyin.com/")
            self.assertEqual(native.call_args.kwargs["query"]["uifid"], "test-anonymous-id")


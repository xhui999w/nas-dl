"""Add the current official detail signature; retain yt-dlp's media handling."""
from yt_dlp.extractor.tiktok import DouyinIE as _UpstreamDouyinIE
from yt_dlp.utils import ExtractorError as _ExtractorError

from server.douyin_signing import signed_detail_query


class _SignedDouyinIE(_UpstreamDouyinIE, plugin_name="nasflow_signature"):
    def _download_json(self, url, video_id, *args, **kwargs):
        if url == "https://www.douyin.com/aweme/v1/web/aweme/detail/":
            cookies = self._get_cookies("https://www.douyin.com/")
            identifier = cookies.get("UIFID") or cookies.get("UIFID_TEMP")
            if not identifier:
                self._download_webpage("https://www.douyin.com/", video_id,
                                       note="Reading website request identifier", fatal=False)
                cookies = self._get_cookies("https://www.douyin.com/")
                identifier = cookies.get("UIFID") or cookies.get("UIFID_TEMP")
            if not identifier:
                raise _ExtractorError("Fresh cookies are needed：缺少网站请求标识，请更新抖音 Cookie", expected=True)
            kwargs["query"] = signed_detail_query(kwargs.get("query") or {}, identifier.value)
        return super()._download_json(url, video_id, *args, **kwargs)

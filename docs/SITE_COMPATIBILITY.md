# NASFlow 网站兼容性报告

> 本报告由 `tests/site_compatibility/run.py` 在无账号、无 Cookie、无 DRM 绕过的环境中实际执行生成。
> `download=✅` 表示使用 yt-dlp `--test` 实际请求了 10 KiB 媒体数据；仅元数据通过不等同于完整下载验证。

## 测试环境

- yt-dlp：`2026.08.19`
- gallery-dl：`1.32.9`
- 本地 FFmpeg：`不可用（Docker 镜像内已安装）`
- 测试用例：39 个，覆盖 29 个平台
- 网络：当前中国大陆网络；海外站点通过当前系统网络环境访问

## 统一状态

`PASS`、`PARTIAL`、`FAIL`、`UNSUPPORTED`、`LOGIN_REQUIRED`、`COOKIE_REQUIRED`、`DRM`、`REGION_LOCKED`、`BLOCKED`、`TEST_URL_INVALID`、`NETWORK_ERROR`、`UNKNOWN`。

## 平台汇总

| 网站 | 普通视频 | 短视频 | 合集/列表 | 字幕 | Cookie | 实际下载 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Bilibili | ✅ | — | ✅ | — | 可选/未使用 | ✅ | PASS | 正常 |
| Douyin | — | — | — | — | 需要 | — | COOKIE_REQUIRED | COOKIE_REQUIRED |
| Ixigua | — | — | — | — | 需要 | — | COOKIE_REQUIRED | COOKIE_REQUIRED |
| Xiaohongshu | — | — | — | — | 可选/未使用 | — | FAIL | PARSE_ERROR |
| Weibo | ✅ | — | — | — | 可选/未使用 | ✅ | PASS | 正常 |
| Weishi | — | — | — | — | 可选/未使用 | — | UNSUPPORTED | UNSUPPORTED |
| Toutiao | ✅ | — | — | — | 可选/未使用 | ✅ | PASS | 正常 |
| AcFun | ✅ | — | — | — | 可选/未使用 | ✅ | PASS | 正常 |
| Haokan | — | — | — | — | 可选/未使用 | — | UNSUPPORTED | UNSUPPORTED |
| Kuaishou | — | — | — | — | 可选/未使用 | — | UNSUPPORTED | UNSUPPORTED |
| iQIYI | — | — | — | — | 可选/未使用 | — | FAIL | PARSE_ERROR |
| Youku | — | — | — | — | 可选/未使用 | — | NETWORK_ERROR | NETWORK_ERROR |
| Tencent Video | — | — | — | — | 可选/未使用 | — | UNSUPPORTED | UNSUPPORTED |
| Sohu Video | — | — | — | — | 可选/未使用 | — | FAIL | DOWNLOADER_ERROR |
| MangoTV | — | — | — | — | 可选/未使用 | — | UNSUPPORTED | UNSUPPORTED |
| Yangshipin | — | — | — | — | 可选/未使用 | — | UNSUPPORTED | UNSUPPORTED |
| 1905 | — | — | — | — | 可选/未使用 | — | BLOCKED | BLOCKED |
| CCTV | ✅ | — | — | — | 可选/未使用 | ✅ | PASS | 正常 |
| YouTube | ✅ | ✅ | ✅ | ✅ | 可选/未使用 | ✅ | PASS | 正常 |
| Vimeo | — | — | — | — | 可选/未使用 | — | BLOCKED | BLOCKED |
| TikTok | — | — | — | — | 可选/未使用 | — | BLOCKED | BLOCKED、UNSUPPORTED |
| Twitch | — | ✅ | — | — | 可选/未使用 | ✅ | PASS | 正常 |
| Instagram | — | ✅ | ✅ | — | 可选/未使用 | ✅ | PASS | 正常 |
| Facebook | — | ✅ | — | — | 可选/未使用 | — | PARTIAL | PARSE_ERROR |
| Dailymotion | ✅ | — | — | — | 可选/未使用 | ✅ | PASS | 正常 |
| X / Twitter | ✅ | — | — | — | 可选/未使用 | ✅ | PASS | 正常 |
| Pinterest | ✅ | — | ⚠️ | — | 可选/未使用 | ✅ | PARTIAL | NETWORK_ERROR |
| Reddit | ✅ | — | — | — | 可选/未使用 | ✅ | PASS | 正常 |
| SoundCloud | ✅ | — | ⚠️ | — | 可选/未使用 | ✅ | PARTIAL | DRM |

## 用例明细

| 网站 | URL 类型 | 引擎/Extractor | 元数据 | 下载 | 合并能力 | 字幕 | 封面 | 结果 | 错误分类 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Bilibili | playlist | BilibiliPlaylist | ✅ | 未执行 | — | — | — | PASS | — |
| Bilibili | multi_part | BiliBili | ✅ | 未执行 | — | — | — | PASS | — |
| Bilibili | single | BiliBili | ✅ | ✅ | ✅ | — | ✅ | PASS | — |
| Douyin | short_video | yt-dlp | — | 未执行 | — | — | — | COOKIE_REQUIRED | COOKIE_REQUIRED |
| Douyin | share_url | yt-dlp | — | 未执行 | — | — | — | COOKIE_REQUIRED | COOKIE_REQUIRED |
| Ixigua | single | yt-dlp | — | 未执行 | — | — | — | COOKIE_REQUIRED | COOKIE_REQUIRED |
| Xiaohongshu | note_video | yt-dlp | — | 未执行 | — | — | — | FAIL | PARSE_ERROR |
| Weibo | single | Weibo | ✅ | ✅ | — | — | ✅ | PASS | — |
| Weishi | single | yt-dlp | — | 未执行 | — | — | — | UNSUPPORTED | UNSUPPORTED |
| Toutiao | single | Toutiao | ✅ | ✅ | — | — | ✅ | PASS | — |
| AcFun | single | AcFunVideo | ✅ | ✅ | — | — | ✅ | PASS | — |
| Haokan | single | yt-dlp | — | 未执行 | — | — | — | UNSUPPORTED | UNSUPPORTED |
| Kuaishou | short_video | yt-dlp | — | 未执行 | — | — | — | UNSUPPORTED | UNSUPPORTED |
| iQIYI | single | yt-dlp | — | 未执行 | — | — | — | FAIL | PARSE_ERROR |
| Youku | single | yt-dlp | — | 未执行 | — | — | — | NETWORK_ERROR | NETWORK_ERROR |
| Tencent Video | landing | yt-dlp | — | 未执行 | — | — | — | UNSUPPORTED | UNSUPPORTED |
| Sohu Video | single | yt-dlp | — | 未执行 | — | — | — | FAIL | DOWNLOADER_ERROR |
| MangoTV | landing | yt-dlp | — | 未执行 | — | — | — | UNSUPPORTED | UNSUPPORTED |
| Yangshipin | landing | yt-dlp | — | 未执行 | — | — | — | UNSUPPORTED | UNSUPPORTED |
| 1905 | single | yt-dlp | — | 未执行 | — | — | — | BLOCKED | BLOCKED |
| CCTV | single | CCTV | ✅ | ✅ | — | — | — | PASS | — |
| YouTube | shorts | Youtube | ✅ | ✅ | ✅ | — | ✅ | PASS | — |
| YouTube | playlist | YoutubeTab | ✅ | 未执行 | — | — | ✅ | PASS | — |
| YouTube | single_query | Youtube | ✅ | ✅ | ✅ | ✅ | ✅ | PASS | — |
| Vimeo | single | yt-dlp | — | 未执行 | — | — | — | BLOCKED | BLOCKED |
| TikTok | short_video | yt-dlp | — | 未执行 | — | — | — | BLOCKED | BLOCKED |
| Twitch | clip | TwitchClips | ✅ | ✅ | — | — | ✅ | PASS | — |
| TikTok | short_link | yt-dlp | — | 未执行 | — | — | — | UNSUPPORTED | UNSUPPORTED |
| Instagram | user | gallery-dl | ✅ | 未执行 | — | — | — | PASS | — |
| Facebook | single | yt-dlp | — | 未执行 | — | — | — | FAIL | PARSE_ERROR |
| Instagram | reel | Instagram | ✅ | ✅ | — | — | ✅ | PASS | — |
| Dailymotion | single | Dailymotion | ✅ | ✅ | — | — | ✅ | PASS | — |
| Facebook | reel | Facebook | ✅ | 未执行 | — | — | ✅ | PASS | — |
| X / Twitter | single | Twitter | ✅ | ✅ | — | — | ✅ | PASS | — |
| Pinterest | pin | Pinterest | ✅ | ✅ | — | — | ✅ | PASS | — |
| Reddit | single | Reddit | ✅ | ✅ | ✅ | — | ✅ | PASS | — |
| SoundCloud | playlist | yt-dlp | — | 未执行 | — | — | — | DRM | DRM |
| SoundCloud | track | Soundcloud | ✅ | ✅ | — | — | ✅ | PASS | — |
| Pinterest | collection | yt-dlp | — | 未执行 | — | — | — | NETWORK_ERROR | NETWORK_ERROR |

## 受限与失败原因

| 网站 | URL 类型 | 状态 | 具体原因 |
| --- | --- | --- | --- |
| Douyin | short_video | COOKIE_REQUIRED | 底层 extractor 明确要求新鲜 Cookie；未使用账号继续测试 |
| Douyin | share_url | COOKIE_REQUIRED | 底层 extractor 明确要求新鲜 Cookie；未使用账号继续测试 |
| Ixigua | single | COOKIE_REQUIRED | 底层 extractor 明确要求新鲜 Cookie；未使用账号继续测试 |
| Xiaohongshu | note_video | FAIL | 底层 extractor 已匹配网站，但未能解析出可用媒体 |
| Weishi | single | UNSUPPORTED | 当前下载器没有匹配该 URL 的专用 extractor |
| Haokan | single | UNSUPPORTED | 当前下载器没有匹配该 URL 的专用 extractor |
| Kuaishou | short_video | UNSUPPORTED | 当前下载器没有匹配该 URL 的专用 extractor |
| iQIYI | single | FAIL | 底层 extractor 已匹配网站，但未能解析出可用媒体 |
| Youku | single | NETWORK_ERROR | 当前测试网络出现超时、TLS 或连接异常 |
| Tencent Video | landing | UNSUPPORTED | 当前下载器没有匹配该 URL 的专用 extractor |
| Sohu Video | single | FAIL | 底层下载器内部异常 |
| MangoTV | landing | UNSUPPORTED | 当前下载器没有匹配该 URL 的专用 extractor |
| Yangshipin | landing | UNSUPPORTED | 当前下载器没有匹配该 URL 的专用 extractor |
| 1905 | single | BLOCKED | 当前 IP/请求被 403、429 或风控拦截 |
| Vimeo | single | BLOCKED | 当前 IP/请求被 403、429 或风控拦截 |
| TikTok | short_video | BLOCKED | 当前 IP/请求被 403、429 或风控拦截 |
| TikTok | short_link | UNSUPPORTED | 当前下载器没有匹配该 URL 的专用 extractor |
| Facebook | single | FAIL | 底层 extractor 已匹配网站，但未能解析出可用媒体 |
| SoundCloud | playlist | DRM | 资源包含 DRM；未尝试绕过 |
| Pinterest | collection | NETWORK_ERROR | 当前测试网络出现超时、TLS 或连接异常 |

## 架构审计结论

- 调用链：Web 表单 → `POST /api/tasks` → SQLite Task → 线程池 → yt-dlp/gallery-dl 子进程 → 日志/进度写回 SQLite → Web 轮询展示。
- 分流：Instagram Reel/帖子和 X/Twitter status 视频使用 yt-dlp；Instagram/X 用户主页及 Pixiv、Flickr 图集使用 gallery-dl；用户指定视频/图集时可覆盖。
- 视频/音频：按 best、4K、1080p、audio 选择格式；Docker 内 FFmpeg 负责分离音视频合并和音频转码。
- 封面/元数据：yt-dlp 写入 thumbnail 与 info.json；gallery-dl 写 metadata。
- 字幕：当前下载任务未启用 `--write-subs`，测试工具只记录 extractor 是否发现字幕。
- 列表：普通任务固定 `--no-playlist`；订阅任务使用 archive + lazy playlist；目录浏览最多读取 100 项。
- Cookie：Web 按域名单独保存，后端转换为权限受限的 Netscape 文件并按子域匹配；测试不使用真实 Cookie。
- 代理：环境代理继续有效；系统设置中的代理现在会传给 yt-dlp、gallery-dl 和订阅目录读取。
- Referer/User-Agent：交由各下载器 extractor 自动生成，NASFlow 没有写死站点头。
- 文件：`下载目录/分类/uploader/title [id].ext`；Windows/Linux 子进程统一 UTF-8。
- 错误：后端现已输出稳定 `error_type`，区分 Cookie、登录、DRM、地区、风控、网络、失效、未支持、解析、FFmpeg 和下载器异常。

## 本轮修复与回归风险

- 修复 FFmpeg 可用时 `--embed-metadata` 被插入 `--print` 参数对之间的问题；YouTube Shorts 已回归通过。
- Instagram Reel/帖子和 X/Twitter status 改由 yt-dlp 处理，用户主页/图集仍保留 gallery-dl，避免 gallery-dl 空结果被误报完成。
- 系统设置中的 proxy 现在真正传递给下载任务和订阅目录读取；环境变量代理仍保持兼容。
- 新增结构化 `error_type`，前端显示可读原因；旧数据库通过增量字段迁移兼容。
- yt-dlp 最低版本提升到 2026.08.19，gallery-dl 提升到 1.32.9，并加入 curl-cffi impersonation 支持。
- 回归风险：下载器上游 extractor 和站点风控会持续变化；Instagram 非视频图集仍走 gallery-dl；代理配置错误会影响所有外网任务。每周自动矩阵用于尽早发现变化。

## 统计

- PASS: 11
- PARTIAL: 3
- FAIL: 3
- COOKIE_REQUIRED: 2
- LOGIN_REQUIRED: 0
- DRM: 0
- REGION_LOCKED: 0
- BLOCKED: 3
- UNSUPPORTED: 6
- NETWORK_ERROR: 1
- UNKNOWN: 0

## 下一阶段优先适配

1. 抖音：完善 Cookie 导入有效性检测与失效提示。
2. 小红书：跟进 extractor 的视频格式解析失败。
3. 快手：当前没有可用的 yt-dlp 专用 extractor，需要评估可维护的公开解析方案。
4. 爱奇艺：专用 extractor 能识别站点但当前样例无法解析媒体。
5. 好看视频：当前无专用 extractor，优先级高于会员/DRM 为主的长视频平台。

## 复现

```powershell
.\.venv\Scripts\python.exe tests\site_compatibility\run.py --workers 3
.\.venv\Scripts\python.exe tests\site_compatibility\generate_report.py
```

失败原文、测试时间和测试 URL 保存在 `tests/site_compatibility/results.json`。测试 URL 会失效，重新运行时应结合错误分类判断，不能将单个 URL 失效等同于整站不支持。

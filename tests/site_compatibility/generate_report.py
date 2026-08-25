from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).with_name("results.json")
REPORT = ROOT / "docs" / "SITE_COMPATIBILITY.md"


def mark(value: bool) -> str:
    return "✅" if value else "—"


def aggregate(items: list[dict]) -> str:
    statuses = [item["result"] for item in items]
    if all(status == "PASS" for status in statuses):
        return "PASS"
    if "PASS" in statuses:
        return "PARTIAL"
    for status in ("COOKIE_REQUIRED", "LOGIN_REQUIRED", "DRM", "REGION_LOCKED", "BLOCKED", "NETWORK_ERROR"):
        if status in statuses:
            return status
    if all(status == "UNSUPPORTED" for status in statuses):
        return "UNSUPPORTED"
    if "FAIL" in statuses:
        return "FAIL"
    return "UNKNOWN"


def main() -> None:
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in results:
        grouped[item["site"]].append(item)
    platform_statuses = {site: aggregate(items) for site, items in grouped.items()}
    counts = Counter(platform_statuses.values())
    versions = results[0]["downloader_versions"] if results else {}
    lines = [
        "# NASFlow 网站兼容性报告",
        "",
        "> 本报告由 `tests/site_compatibility/run.py` 在无账号、无 Cookie、无 DRM 绕过的环境中实际执行生成。",
        "> `download=✅` 表示使用 yt-dlp `--test` 实际请求了 10 KiB 媒体数据；仅元数据通过不等同于完整下载验证。",
        "",
        "## 测试环境",
        "",
        f"- yt-dlp：`{versions.get('yt_dlp', 'unknown')}`",
        f"- gallery-dl：`{versions.get('gallery_dl', 'unknown')}`",
        f"- 本地 FFmpeg：`{'可用' if versions.get('ffmpeg') else '不可用（Docker 镜像内已安装）'}`",
        f"- 测试用例：{len(results)} 个，覆盖 {len(grouped)} 个平台",
        "- 网络：当前中国大陆网络；海外站点通过当前系统网络环境访问",
        "",
        "## 统一状态",
        "",
        "`PASS`、`PARTIAL`、`FAIL`、`UNSUPPORTED`、`LOGIN_REQUIRED`、`COOKIE_REQUIRED`、`DRM`、`REGION_LOCKED`、`BLOCKED`、`TEST_URL_INVALID`、`NETWORK_ERROR`、`UNKNOWN`。",
        "",
        "## 平台汇总",
        "",
        "| 网站 | 普通视频 | 短视频 | 合集/列表 | 字幕 | Cookie | 实际下载 | 状态 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for site, items in grouped.items():
        ordinary = any(item["metadata_parse"] and item["url_type"] in {"single", "single_query", "track", "pin", "note_video"} for item in items)
        short = any(item["metadata_parse"] and item["url_type"] in {"shorts", "short_video", "reel", "clip"} for item in items)
        collection_cases = [item for item in items if item["url_type"] in {"playlist", "collection", "multi_part", "user"}]
        collection = "✅" if collection_cases and all(item["metadata_parse"] for item in collection_cases) else ("⚠️" if collection_cases else "—")
        cookie = "需要" if any(item["cookie_required"] for item in items) else "可选/未使用"
        downloaded = any(item["download"] is True for item in items)
        errors = sorted({item["error_type"] for item in items if item.get("error_type")})
        note = "正常" if not errors else "、".join(errors)
        lines.append(f"| {site} | {mark(ordinary)} | {mark(short)} | {collection} | {mark(any(item['subtitle'] for item in items))} | {cookie} | {mark(downloaded)} | {platform_statuses[site]} | {note} |")

    lines += [
        "",
        "## 用例明细",
        "",
        "| 网站 | URL 类型 | 引擎/Extractor | 元数据 | 下载 | 合并能力 | 字幕 | 封面 | 结果 | 错误分类 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        extractor = item.get("extractor") or item["engine"]
        download = "未执行" if item["download"] is None else mark(item["download"])
        lines.append(f"| {item['site']} | {item['url_type']} | {extractor} | {mark(item['metadata_parse'])} | {download} | {mark(item['audio_video_merge'])} | {mark(item['subtitle'])} | {mark(item['thumbnail'])} | {item['result']} | {item.get('error_type') or '—'} |")

    lines += [
        "",
        "## 受限与失败原因",
        "",
        "| 网站 | URL 类型 | 状态 | 具体原因 |",
        "| --- | --- | --- | --- |",
    ]
    reason_labels = {
        "COOKIE_REQUIRED": "底层 extractor 明确要求新鲜 Cookie；未使用账号继续测试",
        "LOGIN_REQUIRED": "资源要求登录；未尝试绕过",
        "DRM": "资源包含 DRM；未尝试绕过",
        "REGION_LOCKED": "当前网络地区不可用",
        "BLOCKED": "当前 IP/请求被 403、429 或风控拦截",
        "UNSUPPORTED": "当前下载器没有匹配该 URL 的专用 extractor",
        "NETWORK_ERROR": "当前测试网络出现超时、TLS 或连接异常",
        "PARSE_ERROR": "底层 extractor 已匹配网站，但未能解析出可用媒体",
        "DOWNLOADER_ERROR": "底层下载器内部异常",
        "VIDEO_UNAVAILABLE": "测试资源已删除、私密或不可用",
        "UNKNOWN": "现有证据不足，不能安全归类",
    }
    for item in results:
        if item["result"] != "PASS":
            reason = reason_labels.get(item.get("error_type") or "UNKNOWN", "现有证据不足，不能安全归类")
            lines.append(f"| {item['site']} | {item['url_type']} | {item['result']} | {reason} |")

    lines += [
        "",
        "## 架构审计结论",
        "",
        "- 调用链：Web 表单 → `POST /api/tasks` → SQLite Task → 线程池 → yt-dlp/gallery-dl 子进程 → 日志/进度写回 SQLite → Web 轮询展示。",
        "- 分流：Instagram、X/Twitter、Pixiv、Flickr 默认使用 gallery-dl，其余自动使用 yt-dlp；用户指定视频/图集时可覆盖。",
        "- 视频/音频：按 best、4K、1080p、audio 选择格式；Docker 内 FFmpeg 负责分离音视频合并和音频转码。",
        "- 封面/元数据：yt-dlp 写入 thumbnail 与 info.json；gallery-dl 写 metadata。",
        "- 字幕：当前下载任务未启用 `--write-subs`，测试工具只记录 extractor 是否发现字幕。",
        "- 列表：普通任务固定 `--no-playlist`；订阅任务使用 archive + lazy playlist；目录浏览最多读取 100 项。",
        "- Cookie：Web 按域名单独保存，后端转换为权限受限的 Netscape 文件并按子域匹配；测试不使用真实 Cookie。",
        "- 代理：环境代理继续有效；系统设置中的代理现在会传给 yt-dlp、gallery-dl 和订阅目录读取。",
        "- Referer/User-Agent：交由各下载器 extractor 自动生成，NASFlow 没有写死站点头。",
        "- 文件：`下载目录/分类/uploader/title [id].ext`；Windows/Linux 子进程统一 UTF-8。",
        "- 错误：后端现已输出稳定 `error_type`，区分 Cookie、登录、DRM、地区、风控、网络、失效、未支持、解析、FFmpeg 和下载器异常。",
        "",
        "## 本轮修复与回归风险",
        "",
        "- 修复 FFmpeg 可用时 `--embed-metadata` 被插入 `--print` 参数对之间的问题；YouTube Shorts 已回归通过。",
        "- Instagram Reel/帖子和 X/Twitter status 改由 yt-dlp 处理，用户主页/图集仍保留 gallery-dl，避免 gallery-dl 空结果被误报完成。",
        "- 系统设置中的 proxy 现在真正传递给下载任务和订阅目录读取；环境变量代理仍保持兼容。",
        "- 新增结构化 `error_type`，前端显示可读原因；旧数据库通过增量字段迁移兼容。",
        "- yt-dlp 最低版本提升到 2026.08.19，gallery-dl 提升到 1.32.9，并加入 curl-cffi impersonation 支持。",
        "- 回归风险：下载器上游 extractor 和站点风控会持续变化；Instagram 非视频图集仍走 gallery-dl；代理配置错误会影响所有外网任务。每周自动矩阵用于尽早发现变化。",
        "",
        "## 统计",
        "",
    ]
    for key in ("PASS", "PARTIAL", "FAIL", "COOKIE_REQUIRED", "LOGIN_REQUIRED", "DRM", "REGION_LOCKED", "BLOCKED", "UNSUPPORTED", "NETWORK_ERROR", "UNKNOWN"):
        lines.append(f"- {key}: {counts.get(key, 0)}")
    lines += [
        "",
        "## 下一阶段优先适配",
        "",
        "1. 抖音：完善 Cookie 导入有效性检测与失效提示。",
        "2. 小红书：跟进 extractor 的视频格式解析失败。",
        "3. 快手：当前没有可用的 yt-dlp 专用 extractor，需要评估可维护的公开解析方案。",
        "4. 爱奇艺：专用 extractor 能识别站点但当前样例无法解析媒体。",
        "5. 好看视频：当前无专用 extractor，优先级高于会员/DRM 为主的长视频平台。",
        "",
        "## 复现",
        "",
        "```powershell",
        ".\\.venv\\Scripts\\python.exe tests\\site_compatibility\\run.py --workers 3",
        ".\\.venv\\Scripts\\python.exe tests\\site_compatibility\\generate_report.py",
        "```",
        "",
        "失败原文、测试时间和测试 URL 保存在 `tests/site_compatibility/results.json`。测试 URL 会失效，重新运行时应结合错误分类判断，不能将单个 URL 失效等同于整站不支持。",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

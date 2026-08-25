from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MATRIX_PATH = HERE / "sites.json"
RESULTS_PATH = HERE / "results.json"
sys.path.insert(0, str(ROOT))

from server.download_errors import classify_download_error  # noqa: E402

_VERSION_CACHE: dict[str, str] = {}


def curl_ca_bundle() -> str | None:
    """Give curl-cffi an ASCII-safe CA path on Windows workspaces."""
    try:
        import certifi
    except ImportError:
        return None
    source = Path(certifi.where())
    target = Path(tempfile.gettempdir()) / "nasflow-certifi-ca.pem"
    if not target.exists() or source.stat().st_mtime_ns > target.stat().st_mtime_ns:
        shutil.copyfile(source, target)
    return str(target)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def compact(text: str, limit: int = 1800) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", text).strip()
    return text[-limit:]


def aggregate_result(error_type: str | None, metadata_ok: bool, download_ok: bool | None) -> str:
    if metadata_ok and download_ok is not False:
        return "PASS"
    if metadata_ok:
        return "PARTIAL"
    mapping = {
        "COOKIE_REQUIRED": "COOKIE_REQUIRED",
        "LOGIN_REQUIRED": "LOGIN_REQUIRED",
        "DRM": "DRM",
        "REGION_LOCKED": "REGION_LOCKED",
        "BLOCKED": "BLOCKED",
        "NETWORK_ERROR": "NETWORK_ERROR",
        "VIDEO_UNAVAILABLE": "TEST_URL_INVALID",
        "UNSUPPORTED": "UNSUPPORTED",
    }
    return mapping.get(error_type or "", "FAIL" if error_type != "UNKNOWN" else "UNKNOWN")


def run_process(command: list[str], timeout: int) -> tuple[int, str, float]:
    started = time.monotonic()
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    ca_bundle = curl_ca_bundle()
    if ca_bundle:
        env.update({"SSL_CERT_FILE": ca_bundle, "CURL_CA_BUNDLE": ca_bundle, "REQUESTS_CA_BUNDLE": ca_bundle})
    try:
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
        return process.returncode, process.stdout + "\n" + process.stderr, time.monotonic() - started
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + "\n" + (exc.stderr or "")
        return 124, output + "\nERROR: compatibility test timed out", time.monotonic() - started


def yt_metadata(case: dict) -> tuple[int, str, float, dict]:
    command = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--skip-download", "--playlist-end", "3", "--socket-timeout", "12", "--retries", "1", "--extractor-retries", "1", case["url"]]
    code, output, duration = run_process(command, 45)
    parsed = {}
    if code == 0:
        for line in reversed(output.splitlines()):
            try:
                candidate = json.loads(line)
                if isinstance(candidate, dict):
                    parsed = candidate
                    break
            except json.JSONDecodeError:
                continue
    return code, output, duration, parsed


def gallery_metadata(case: dict) -> tuple[int, str, float, dict]:
    command = [sys.executable, "-m", "gallery_dl", "--quiet", "--simulate", "--range", "1", "--dump-json", case["url"]]
    code, output, duration = run_process(command, 45)
    parsed = {}
    if code == 0:
        try:
            events = json.loads(output)
        except json.JSONDecodeError:
            events = []
        errors = [event[1] for event in events if isinstance(event, list) and event and event[0] == -1]
        metadata = [event[-1] for event in events if isinstance(event, list) and len(event) >= 3 and isinstance(event[-1], dict)]
        if errors or not metadata:
            code = 1
            detail = errors[0] if errors else {"message": "gallery-dl returned no downloadable entries"}
            output += "\nERROR: " + str(detail.get("message") or detail)
        else:
            parsed = metadata[0]
    return code, output, duration, parsed


def bounded_download(case: dict) -> tuple[bool | None, str, float]:
    if not case.get("download"):
        return None, "not requested for this case", 0.0
    if case["engine"] != "yt-dlp":
        return None, "bounded media download is only enabled for yt-dlp cases", 0.0
    with tempfile.TemporaryDirectory(prefix="nasflow-compat-") as target:
        command = [sys.executable, "-m", "yt_dlp", "--test", "--no-playlist", "--socket-timeout", "12", "--retries", "1", "--fragment-retries", "1", "-f", "bv*+ba/b", "-o", str(Path(target) / "%(id)s.%(ext)s"), case["url"]]
        code, output, duration = run_process(command, 75)
        return code == 0, output, duration


def test_case(case: dict) -> dict:
    metadata_runner = gallery_metadata if case["engine"] == "gallery-dl" else yt_metadata
    code, output, parse_seconds, metadata = metadata_runner(case)
    metadata_ok = code == 0
    download_ok, download_output, download_seconds = bounded_download(case) if metadata_ok else (None, "metadata failed; download skipped", 0.0)
    combined_error = compact(output + "\n" + download_output)
    error_type = None if metadata_ok and download_ok is not False else classify_download_error(combined_error)
    formats = metadata.get("formats") or []
    subtitles = metadata.get("subtitles") or metadata.get("automatic_captions") or {}
    thumbnails = metadata.get("thumbnails") or ([] if not metadata.get("thumbnail") else [metadata["thumbnail"]])
    has_video_only = any(item.get("vcodec") not in (None, "none") and item.get("acodec") == "none" for item in formats if isinstance(item, dict))
    has_audio_only = any(item.get("acodec") not in (None, "none") and item.get("vcodec") == "none" for item in formats if isinstance(item, dict))
    return {
        "site": case["site"], "country_region": case["country"], "url_type": case["url_type"],
        "test_url": case["url"], "engine": case["engine"], "extractor": metadata.get("extractor_key") or metadata.get("extractor"),
        "metadata_parse": metadata_ok, "download": download_ok, "audio_video_merge": bool(has_video_only and has_audio_only),
        "subtitle": bool(subtitles), "thumbnail": bool(thumbnails),
        "cookie_required": error_type == "COOKIE_REQUIRED", "login_required": error_type == "LOGIN_REQUIRED",
        "drm": error_type == "DRM", "result": aggregate_result(error_type, metadata_ok, download_ok),
        "error_type": error_type, "error_message": None if error_type is None else combined_error,
        "parse_seconds": round(parse_seconds, 2), "download_seconds": round(download_seconds, 2), "test_time": now(),
        "downloader_versions": {"yt_dlp": yt_version(), "gallery_dl": gallery_version(), "ffmpeg": bool(shutil.which("ffmpeg"))},
    }


def module_version(module: str) -> str:
    if module in _VERSION_CACHE:
        return _VERSION_CACHE[module]
    code, output, _ = run_process([sys.executable, "-m", module, "--version"], 10)
    value = output.strip().splitlines()[0] if code == 0 and output.strip() else "unavailable"
    _VERSION_CACHE[module] = value
    return value


def yt_version() -> str:
    return module_version("yt_dlp")


def gallery_version() -> str:
    return module_version("gallery_dl")


def save(results: list[dict]) -> None:
    RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", action="append", help="Run only the named site (repeatable)")
    parser.add_argument("--resume", action="store_true", help="Keep completed case results")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    if args.site:
        selected = {name.casefold() for name in args.site}
        matrix = [case for case in matrix if case["site"].casefold() in selected]
    existing = json.loads(RESULTS_PATH.read_text(encoding="utf-8")) if args.resume and RESULTS_PATH.exists() else []
    for item in existing:
        if item.get("error_message"):
            item["error_type"] = classify_download_error(item["error_message"])
            item["cookie_required"] = item["error_type"] == "COOKIE_REQUIRED"
            item["login_required"] = item["error_type"] == "LOGIN_REQUIRED"
            item["drm"] = item["error_type"] == "DRM"
            item["result"] = aggregate_result(item["error_type"], item["metadata_parse"], item["download"])
    keys = {(item["site"], item["url_type"], item["test_url"]) for item in existing}
    pending = [case for case in matrix if (case["site"], case["url_type"], case["url"]) not in keys]
    results = existing
    save(results)
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 6))) as pool:
        futures = {pool.submit(test_case, case): case for case in pending}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            save(results)
            print(f"{result['site']:<16} {result['url_type']:<14} {result['result']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

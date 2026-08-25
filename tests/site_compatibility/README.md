# Site compatibility tests

This suite exercises the same downloader engines used by NASFlow without using
private credentials. It persists each case immediately to `results.json`, so an
interrupted run can resume with `--resume`.

```powershell
.\.venv\Scripts\python.exe tests\site_compatibility\run.py
.\.venv\Scripts\python.exe tests\site_compatibility\run.py --resume
.\.venv\Scripts\python.exe tests\site_compatibility\run.py --site YouTube
```

Metadata parsing is always attempted. Cases marked `download: true` also use
the downloader's bounded `--test` mode, which requests 10 KiB of media data.
No cookies, credentials, DRM bypasses, or paywall workarounds are used.

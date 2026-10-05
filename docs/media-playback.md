# 网页在线播放（第一版）

媒体库中，已完成、下载目录内视频文件仍存在的记录显示 `▶`。点击进入
`/media/play/{id}`，使用 HTML5 Video 和本地打包的 Plyr 控件；返回按钮进入 `/#library`。
同一记录也可以创建播放分享链接，进入 `/media/share/{token}`。

## 数据流程和安全

1. SQLite 的 Task 记录已有 `id`、`title`、`url`、`status` 和 `output_path`。
   下载器的 `after_move:__NASFLOW_FILE__` 会记录最终文件路径。
2. 任务响应增加 `media_available`；不修改数据库结构。响应中的 `output_path`
   只返回文件名，`log_tail` 不再返回下载器内部日志。
3. 播放页读取 `/nas-api/api/media/{id}` 的标题、来源和格式信息。
4. Video 的 `src` 指向 `/nas-api/api/media/{id}/stream`。现有同源代理流式转发到
   FastAPI 的 `/api/media/{id}/stream`，不使用 Blob、完整文件下载或媒体副本。
5. 后端始终按 ID 查数据库，校验已完成、文件存在、解析符号链接后的路径仍在
   `NASFLOW_DOWNLOADS` 内。不接受 URL 中的文件路径，也不返回真实绝对路径。
   沿用现有部署的访问控制，不另建公共文件目录。

## HTTP Range

复用现有 FastAPI 所依赖的 Starlette FileResponse。该响应分块读取原文件，
返回 `Accept-Ranges: bytes`；有效 Range 返回 `206`、`Content-Range` 和片段长度。
支持定长、中间、开放结尾和后缀范围，越界返回 `416`，支持 `If-Range` 和 `HEAD`。
响应为对应视频 MIME 和 `Content-Disposition: inline`；原下载接口仍为 attachment。
现有 `/nas-api` 代理保留 Range/If-Range 和响应头，直接转发响应流，不把视频读入内存。

## 格式和观看进度

- 优先 MP4/M4V：H.264 视频 + AAC/MP3 音频；也支持没有音轨的视频。
- WebM：VP8/VP9/AV1 + Opus/Vorbis；OGV：Theora + Vorbis/Opus。
- 具体解码能力取决于浏览器、设备和编码配置。浏览器错误会显示友好提示。
- MKV、MOV、AVI、WMV、FLV、TS 等容器第一版不直接播放。检测到 HEVC/H.265、
  DTS 等不在上述范围的编码也会提示下载后观看，不转码、不生成 HLS。
- 复用镜像已有的 ffprobe 只读检查编码，结果按文件大小/修改时间缓存最多 128 项。
  ffprobe 不可用时，容器初筛后交给浏览器判断。
- 每 5 秒、暂停、拖动、离页时保存位置到当前浏览器的
  `nasflow:playback:v1:{id}` localStorage。再次打开恢复位置；播放结束清除记录。
  存储不可用不影响播放。不同设备/浏览器之间不同步进度。
- 倍速、音量、全屏、画中画均使用 Plyr 控件；画中画取决于浏览器支持。

## 限次分享

- 分享记录只保存随机链接令牌的 SHA-256 摘要；原始令牌仅在创建时返回并在页面显示一次。
- 访问分享页或预览标题不消耗次数。点击“开始播放”时，后端用原子数据库更新占用一次额度；
  链接创建者可以查看使用量并撤销链接。撤销后，已有播放会话也不能继续请求视频流。
- 成功开始后，浏览器获得仅适用于该链接播放接口的 HttpOnly、Secure Cookie。相同浏览器在
  8 小时内重开或拖动进度不重复扣次；过期后再次开始会作为新播放次数。额度耗尽后，已有会话可续播，
  新浏览器会被拒绝。
- 次数限制的是浏览器播放会话，不是可验证的自然人数。接收者仍可转发链接；每个新的浏览器会占用一次，
  创建者可通过设置低次数和及时撤销来限制传播。链接不设置自动过期时间。
- 分享接口仍按分享记录关联的任务 ID 解析文件，并使用和普通播放相同的下载目录校验与 Range 流式读取，
  不让访问者提交服务器路径。

## 验证

后端安装现有 server/requirements.txt 和测试用 httpx 后运行：

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
```

前端运行 `npm run build`。手动检查：

1. 媒体库中只有已完成且文件存在的视频显示播放图标；缺失文件、图片和失败任务不显示。
2. MP4 点击播放，暂停、拖动、调音量、切倍速、全屏和可用的画中画。
3. 暂停后返回媒体库，再打开同一条视频，确认恢复观看位置。
4. 打开 MKV/不兼容编码，确认提示与原有下载入口；移走文件后刷新，确认图标消失。
5. 浏览器网络面板查看 stream 请求的 `206` 和 `Content-Range`，确认拖动仍可播放。
6. 可检查 `curl -i -H 'Range: bytes=1000-1099' '<网站>/nas-api/api/media/<id>/stream'`，
   返回 100 字节而非整个文件。不存在的 ID、目录外路径不能读取任何文件。

不改变现有下载格式选择、下载任务调度、Cookies、订阅或 Obsidian 处理。

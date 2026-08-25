# NASFlow

把喜欢的内容带回家。NASFlow 是从零实现的 NAS 媒体采集与自动整理中心，使用独立业务代码，通过 `yt-dlp` 与 `gallery-dl` 调用公开下载引擎。

## 当前能力

- 视频与图集链接自动分流
- YouTube JavaScript 挑战支持（Deno + yt-dlp-ejs）
- 下载队列、实时进度和历史记录
- 任务取消、失败重试与可读错误信息
- 服务重启后自动恢复未完成任务
- 可配置并发下载队列
- 画质选择、仅音频模式和自定义保存目录
- 自动写入媒体信息、封面与元数据
- SQLite 持久化
- 订阅、系统设置持久化 API
- 响应式中文 Web 控制台
- Docker Compose 部署

## 本地开发

Web 界面：

```bash
npm install
npm run dev
```

下载服务：

```bash
python -m pip install -r server/requirements.txt
uvicorn server.main:app --reload --port 8888
```

## Docker

编辑 `compose.yaml` 中的 NAS 下载目录后执行。镜像同时支持 `amd64` 和 `arm64`：

```bash
docker compose pull
docker compose up -d
```

镜像：

- `ghcr.io/xhui999w/nas-dl-web:latest`
- `ghcr.io/xhui999w/nas-dl-api:latest`

控制台：`http://NAS地址:3000`

API 健康检查：`http://NAS地址:8888/api/health`

如果宿主机的 `8888` 已被占用，可同时修改 API 端口映射与 Web 环境变量。例如：

```yaml
web:
  environment:
    NASFLOW_API_PORT: 18888
api:
  ports:
    - "18888:8888"
```

## 路线图

- 订阅定时调度、增量检测与过滤规则
- NFO/XMP 元数据模板
- Cookies 安全管理
- Bark、Server 酱、Webhook 通知
- 下载去重、限速、并发与夜间计划
- 媒体库扫描与重复内容检测

第三方组件说明见 [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。

网站兼容性并不等同于绕过登录、会员、地区或 DRM 限制。当前实测矩阵、状态定义和可重复执行方法见 [网站兼容性报告](./docs/SITE_COMPATIBILITY.md)，机器可读结果位于 `tests/site_compatibility/results.json`。

NASFlow 自身代码使用 MIT License 发布。

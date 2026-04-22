# Xunlei Thunder Bridge

Mac 迅雷下载中枢：在 Mac 上控制迅雷下载，在服务器上提供 Web 管理页，通过 WebSocket 实时显示任务、速率、状态，并在下载完成后迁移资源到 Jellyfin 媒体库。

## 功能

- Web 页面在线添加 `magnet/http/https/ftp/ed2k/thunder` 下载任务。
- Mac 代理自动打开迅雷并点击“立即下载”。
- 实时读取迅雷任务库，显示下载进度、速率、任务状态和文件清单。
- 单文件任务完成后自动迁移到服务器媒体库。
- 多文件任务完成后不自动迁移，需要在页面手动选择某个文件迁移。
- 页面提供“清空迅雷下载目录”危险按钮，必须二次确认并输入 `清空`。
- Mac 到服务器使用 SSH/rsync 迁移文件，迁移成功后清理 Mac 本地文件。

## 目录

- `server/`：Node.js WebSocket 服务和前端页面。
- `mac/`：Mac 迅雷采集与控制代理，以及 launchd 配置。
- `docs/DEPLOYMENT.md`：部署说明。

## 快速启动

1. 在服务器上部署 `server/`：

```bash
cd server
docker compose up -d
```

2. 在 Mac 上部署 `mac/thunder-agent.py` 到 `~/.thunder-bridge/thunder-agent.py`。

3. 修改 Mac 配置 `~/.thunder-bridge/config.json`，确保 `server_ws`、`token`、`remote_host`、`remote_user`、`remote_root`、`ssh_key` 正确。

4. 加载 launchd：

```bash
cp mac/com.codex.thunder-bridge.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.codex.thunder-bridge.plist
```

5. 打开页面：

```text
http://服务器IP:8098
```

## 安全提醒

仓库里的 `THUNDER_BRIDGE_TOKEN` 默认是 `change-me`，正式部署时请修改为你自己的共享密钥，并保持服务器端和 Mac 端一致。

# 部署说明

下面以当前家庭影院部署为例：

- Mac：`192.168.1.2`
- 服务器：`192.168.1.18`
- 页面端口：`8098`
- 服务器媒体目录：`/srv/media/movies`

## 服务器端

复制 `server/` 到服务器，例如：

```bash
sudo mkdir -p /opt/thunder-bridge
sudo chown -R "$USER:$USER" /opt/thunder-bridge
cp -r server/* /opt/thunder-bridge/
cd /opt/thunder-bridge
docker compose up -d
```

如果要更换共享密钥，修改 `server/docker-compose.yml`：

```yaml
THUNDER_BRIDGE_TOKEN: "your-secret-token"
```

## Mac 端

复制代理：

```bash
mkdir -p ~/.thunder-bridge ~/Library/LaunchAgents
cp mac/thunder-agent.py ~/.thunder-bridge/
cp mac/com.codex.thunder-bridge.plist ~/Library/LaunchAgents/
chmod +x ~/.thunder-bridge/thunder-agent.py
```

第一次运行会生成：

```text
~/.thunder-bridge/config.json
```

重点配置：

```json
{
  "server_ws": "ws://192.168.1.18:8098/ws",
  "token": "your-secret-token",
  "download_root": "/Users/jiangguiqi/Downloads",
  "remote_host": "192.168.1.18",
  "remote_user": "jiangguiqi",
  "remote_root": "/srv/media/movies",
  "ssh_key": "/Users/jiangguiqi/.ssh/thunder_bridge_ed25519"
}
```

加载代理：

```bash
launchctl unload ~/Library/LaunchAgents/com.codex.thunder-bridge.plist 2>/dev/null || true
launchctl load -w ~/Library/LaunchAgents/com.codex.thunder-bridge.plist
```

## SSH 免密迁移

Mac 生成专用密钥：

```bash
ssh-keygen -t ed25519 -N '' -f ~/.ssh/thunder_bridge_ed25519
```

把 `~/.ssh/thunder_bridge_ed25519.pub` 加入服务器用户的：

```text
~/.ssh/authorized_keys
```

验证：

```bash
ssh -i ~/.ssh/thunder_bridge_ed25519 jiangguiqi@192.168.1.18 'echo OK'
```

## macOS 辅助功能授权

为了让代理自动点击迅雷“立即下载”，需要在：

```text
系统偏好设置 -> 安全性与隐私 -> 隐私 -> 辅助功能
```

允许以下项目：

- `/usr/bin/osascript`
- `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.8/Resources/Python.app`
- `/usr/libexec/sshd-keygen-wrapper`
- `/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/AE.framework/Versions/A/Support/AEServer`

## 行为规则

- 单文件任务完成后，代理等待文件稳定，再自动 `rsync` 到服务器。
- 多文件任务完成后，不自动迁移；页面显示文件列表，手动选择文件迁移。
- 手动迁移只删除被迁移的 Mac 本地文件。
- “清空迅雷下载目录”会删除 `download_root` 下所有一级文件和目录，必须输入 `清空` 确认。

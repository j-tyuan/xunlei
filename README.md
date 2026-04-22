# Xunlei Thunder Bridge

## 实现文档入口

- 总体实现与当前真实链路：`docs/IMPLEMENTATION.md`
- 迅雷通道边界与风险记录：`docs/THUNDER_CHANNEL_INVENTORY_2026-04-22.md`
- 当前主线与阶段性结论：`MAINLINE_TASK.md`

## 当前实现摘要

当前系统已经不是“页面直接操作迅雷 UI”的简单壳子，而是四段式架构：

1. `server/frontend/`
   - `Vue 3 + Element Plus` 控制台源码
2. `server/server.js`
   - REST + WebSocket 中继
3. `mac/thunder-agent.py`
   - Mac 常驻 agent
4. `Thunder.app + DownloadService.xpc`
   - 实际下载执行面

其中迅雷侧现在分成三条不同技术路径：

- 普通 URL 创建：
  - 通过 LLDB 附加 `Thunder` 主进程
  - 在主线程上调用 `Thunder.BaseHostController.createTask:completion:`
- 磁力/BT 文件预览：
  - 打开迅雷预览窗口
  - 用辅助功能读取文件列表
  - 在页面里勾选后再由 agent 回写确认
- 开始 / 暂停 / 删除：
  - 通过 LLDB 附加 `DownloadService.xpc`
  - 调用内部 `etm_start_task / etm_stop_task / etm_delete_task / etm_destroy_task`

前端已经统一改成“命令回执驱动”：

- 页面调用 REST API 只代表命令已下发
- 最终成功与否以 agent 回传的 `commandId` 事件为准
- 全局 loading、toast、任务状态都等真实回执，不再乐观更新

这是一个家庭媒体下载桥接项目：

- 页面运行在服务器上
- 下载实际发生在 Mac 上的迅雷
- 页面通过 WebSocket 实时显示任务、速度、状态和迁移记录
- 下载完成后，资源可以迁移到 Jellyfin 媒体库

## 现在的任务创建方式

当前默认已经接入 `direct` 模式。

页面点击“立即下载”后的链路是：

1. 浏览器 `POST /api/tasks`
2. `server/server.js` 把命令通过 WebSocket 下发给 Mac agent
3. `mac/thunder-agent.py` 优先走 `task_create_mode=direct`
4. agent 通过 `lldb` 附加到 Thunder 主进程
5. 在 Thunder 主队列里实例化 `Thunder.BaseHostController`
6. 用普通下载参数字典调用 `createTask:completion:`
7. Thunder 直接创建任务，不再依赖“新建下载任务”弹窗

页面右侧统计区会显示当前创建方式：

- `直连创建`
- `直连创建 / 可回退`
- `打开链接`

## direct 模式是怎么实现的

`direct` 模式只走普通本地下载路径，不碰 VIP、加速、云下载、离线下载等红线入口。

Mac agent 在接到任务后会：

1. 确认 Thunder 已启动
2. 把任务参数写入临时 JSON 文件
3. 生成一份临时 `.lldb` 脚本
4. 让 LLDB 附加到 Thunder 主进程
5. 在 Thunder 主线程 `dispatch_async(dispatch_get_main_queue(), ...)`
6. 组装普通创建参数：
   - `url`
   - `fileName`
   - `saveDirPath`
   - `fileSize`
7. 调用 `Thunder.BaseHostController.createTask:completion:`
8. 用回调标记文件和 Thunder 本地数据库双重确认任务是否创建成功

这个方案的关键点不是“在 LLDB 里直接执行 createTask”，而是：

- 先附加
- 再把创建动作投递回 Thunder 自己的主队列
- 让真正的创建逻辑在 Thunder 正常运行态里执行

这样可以绕开之前卡住的“弹窗 + 自动点立即下载”路径。

## fallback 逻辑

如果 `direct` 创建失败，并且配置里允许回退：

- agent 会退回到原来的 `open -a Thunder <url>`
- 再尝试 AppleScript 自动确认弹窗

也就是说现在的策略是：

- 优先 `direct`
- 失败才 `open-url`

## Mac agent 关键配置

`~/.thunder-bridge/config.json`

```json
{
  "server_ws": "ws://192.168.1.18:8098/ws",
  "token": "change-me",
  "download_root": "/Users/jiangguiqi/Downloads",
  "task_create_mode": "direct",
  "direct_create_timeout_seconds": 20,
  "direct_create_fallback_to_ui": true,
  "auto_confirm_thunder": true,
  "auto_confirm_seconds": 12
}
```

说明：

- `task_create_mode`
  - `direct`: 默认，优先无弹窗直连创建
  - `open-url`: 强制走旧方案
- `direct_create_timeout_seconds`
  - 等待 direct 创建完成确认的超时时间
- `direct_create_fallback_to_ui`
  - `true`: direct 失败后自动回退
  - `false`: direct 失败即报错，不走弹窗

## 页面已经接入的状态

页面目前已经直接消费 agent 上报的创建模式：

- 顶部统计区新增“创建方式”
- 日志窗口新增 `[create] mode=... fallback=...`
- Web 控制台前端已经迁移到 `Vue 3 + Element Plus`
- 前端源码位于 `server/frontend/`
- `server/public/` 只保存 Vite 构建产物，不再直接手改

因此从页面上就能看出当前是：

- 直连创建
- 直连创建并允许回退
- 还是已经降级到打开链接方案

## 目录

- `server/`
  - Node.js WebSocket 服务
- `server/frontend/`
  - Vue 3 + Element Plus 前端源码
- `server/public/`
  - 前端构建产物，由 `server/frontend` 编译生成
- `mac/`
  - Mac 迅雷采集与控制代理
- `docs/DEPLOYMENT.md`
  - 部署说明
- `MAINLINE_TASK.md`
  - 当前主线任务记录

## 本地运行

### 前端构建

页面现在采用 `Vue 3 + Element Plus + Vite`：

```bash
cd server/frontend
npm install
npm run build
```

构建完成后，静态文件会输出到：

```text
server/public/
```

### 服务器

```bash
cd server
docker compose up -d
```

### Mac

1. 把 `mac/thunder-agent.py` 部署到 `~/.thunder-bridge/thunder-agent.py`
2. 配好 `~/.thunder-bridge/config.json`
3. 加载 launchd

```bash
cp mac/com.codex.thunder-bridge.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.codex.thunder-bridge.plist
```

页面地址：

```text
http://<server-ip>:8098
```

## direct 模式的系统要求

Mac 侧需要满足：

- 已安装 Thunder
- 已安装 Xcode Command Line Tools
- 可用 `lldb`
- 已允许开发调试能力

常见准备项：

```bash
sudo DevToolsSecurity -enable
sudo dseditgroup -o edit -a "$USER" -t user _developer
```

如果这些权限缺失，`direct` 可能失败，然后按配置回退到 `open-url`。

## 安全边界

这个项目只允许普通用户可见的本地下载控制：

- 创建普通下载任务
- 查看任务状态
- 迁移本地文件

不允许：

- 调用 VIP/加速/超级通道
- 模拟或伪造 token
- 修改 Thunder 二进制、签名、数据库来获取额外能力
- 绕过付费、区域、版权或访问控制

更完整的记录见：

- `MAINLINE_TASK.md`
- `docs/THUNDER_CHANNEL_INVENTORY_2026-04-22.md`

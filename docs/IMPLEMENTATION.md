# 项目实现逻辑（2026-04-23）

本文档记录当前 `xunlei` 项目的真实落地实现，重点说明 Web 控制台、Node 中继、Mac agent 与迅雷之间的链路，以及迅雷侧当前已经验证并正在使用的技术路径。

## 1. 总体架构

系统当前由四层组成：

1. 浏览器前端
   - 代码在 `server/frontend/`
   - 技术栈是 `Vue 3 + Element Plus + Vite`
   - 所有交互统一通过 `useThunderBridge.js` 管理

2. 服务端中继
   - 代码在 `server/server.js`
   - 负责提供静态页面、REST API、WebSocket
   - 不直接操作迅雷，只负责下发命令、汇总状态、广播快照

3. Mac 侧 agent
   - 代码在 `mac/thunder-agent.py`
   - 通过 `launchd` 常驻
   - 负责控制迅雷、读取迅雷数据库、管理预览窗口状态、执行文件迁移

4. 迅雷与媒体落地层
   - 迅雷主进程：`/Applications/Thunder.app/Contents/MacOS/Thunder`
   - 迅雷 XPC 下载进程：`/Applications/Thunder.app/Contents/XPCServices/DownloadService.xpc/Contents/MacOS/DownloadService`
   - 本地状态来源：`~/Library/Application Support/Thunder/etm3/etm_map.db`
   - 远端媒体库默认迁移到：`/srv/media/zhixingheyi`

## 2. 端到端命令链路

当前所有前端操作都遵循同一条命令链路：

1. 前端调用 REST API
   - 例如 `POST /api/tasks`
   - 或 `POST /api/tasks/preview`
   - 或 `POST /api/tasks/control`

2. Node 服务端生成 `commandId`
   - `server/server.js` 为每次请求分配唯一 `id`
   - 然后通过 `/ws` 广播给在线的 Mac agent

3. 前端不再把“命令发出去”视为成功
   - 前端会一直等待 agent 回传的最终事件
   - 最终状态以事件里的 `commandId + status` 为准
   - 成功状态：`accepted`、`ready`、`cancelled`、`done`
   - 失败状态：`failed`、`error`

4. Mac agent 执行后回传事件与快照
   - 事件用于结束 loading、显示 toast、给出明确结果
   - 快照用于刷新任务列表、预览窗口、迁移记录、统计信息

这意味着当前前端已经不是“乐观更新”模式，而是“命令回执驱动”模式。页面上的全局 loading 和消息提示，都是建立在 agent 的真实反馈之上。

## 3. 迅雷侧的实现逻辑

### 3.1 HTTP/HTTPS/FTP 任务的直连创建

普通 URL 下载当前走的是“主进程注入创建”：

1. agent 判断链接协议
   - `http` / `https` / `ftp` 允许走 `direct`
   - `magnet` / `ed2k` / `thunder` 不走这条路径

2. agent 等待迅雷主进程启动
   - 目标进程是 `Thunder.app` 主进程

3. agent 生成临时 JSON 与 LLDB 脚本
   - JSON 里写入 `url`、`fileName`、`saveDirPath`
   - LLDB 脚本里会实例化 `Thunder.BaseHostController`

4. 通过 LLDB 附加主进程并执行 Objective-C 表达式
   - 构造 `codexTask` 字典
   - 使用的关键字段包括：
     - `DSKeyCreateTaskURL`
     - `DSKeyCreateTaskFileName`
     - `DSKeyCreateTaskFilePath`
     - `DSKeyCreateTaskFileSize`
   - 同时写入 `source = codex-thunder-bridge/direct`

5. 不直接在 LLDB 当前线程里调用创建方法
   - 而是明确 `dispatch_async(dispatch_get_main_queue(), ...)`
   - 然后在迅雷自己的主队列上调用：
     - `Thunder.BaseHostController.createTask:completion:`

6. 创建结果通过“双确认”判定
   - 一路看回调标记文件
   - 一路检查 `etm_task` 是否出现新的任务行

7. 如果直连创建失败且配置允许回退
   - 才会退回到 `open -a Thunder <url>`

当前这条路径的核心不是“暴力执行方法”，而是“把普通创建动作重新投递回迅雷自己的主线程上下文里执行”。

### 3.2 磁力 / ED2K / thunder 链接的文件预览

这类任务当前还没有走到完全无 UI 的 BT 文件选择 API，因此实现是“打开迅雷预览窗口 + 稳定读取 + 前端回显 + 再次确认”：

1. agent 调用：
   - `open -a Thunder <url>`

2. 迅雷弹出“新建下载任务”窗口
   - 这个窗口仍然是当前磁力/BT 文件选择的事实入口

3. agent 通过辅助功能读取窗口内容
   - 用 `System Events` 枚举迅雷窗口
   - 找到预览窗口
   - 读取 `outline` 里的每一行文件
   - 提取：
     - 行号
     - 勾选状态
     - 文件名
     - 类型
     - 大小

4. 解析结果落到 `pendingDialogs`
   - agent 会给每个待确认窗口分配稳定 `id`
   - 同时根据窗口内容生成 `signature`
   - 多次轮询时会做签名合并，避免前端反复闪烁、重建

5. 前端显示待确认窗口
   - 用户在页面里勾选文件
   - 页面通过 `confirmPreviewTask` 或 `cancelPreviewTask` 下发命令

6. agent 再次通过辅助功能回写 UI
   - 逐行勾选/取消勾选
   - 点击“立即下载 / 下载 / 确定 / 添加”等按钮
   - 或执行关闭/取消逻辑

### 3.3 辅助功能权限与 localhost SSH 回退

macOS 下从 `launchd` 直接跑 `osascript`，经常会遇到辅助功能权限不继承的问题。当前 agent 已经内置了回退机制：

1. 默认先尝试直接执行 `osascript`
2. 如果检测到辅助功能被拒绝
3. 就切换为：
   - 通过 `ssh 127.0.0.1` 回到登录用户会话
   - 在用户上下文里执行 `/usr/bin/osascript`
4. 一旦这条路径可用，agent 会缓存并优先复用这条通道

这就是现在日志里会出现 `direct` 或 `localhost-ssh` 两种访问模式的原因。

### 3.4 任务开始 / 暂停 / 删除的真实控制路径

这部分已经不再使用早期验证阶段那条 `BaseHostController.startTasks / stopTasks / deleteTasks` 路线。

当前正式使用的是：

1. 定位 `DownloadService.xpc` 进程
2. LLDB 附加到该 XPC 进程
3. 直接注入调用内部 `etm_*` 函数

当前实际接入的函数是：

- `etm_start_task`
- `etm_stop_task`
- `etm_delete_task`
- `etm_destroy_task`

对应关系如下：

- `start` -> `etm_start_task`
- `pause` -> `etm_stop_task`
- `delete` 且不删文件 -> `etm_delete_task`
- `delete` 且同时删文件 -> `etm_destroy_task`

执行完成后，agent 不会只看 LLDB 返回值，而是继续轮询迅雷状态确认结果：

- `pause` 要看到任务状态真的进入 `paused`
- `start` 要看到任务重新进入 `waiting / downloading / completed`
- `delete` 要看到任务从状态源里消失

这条路径的意义是：

- 不用坐标点击
- 不依赖前台窗口焦点
- 不走早期那条“迅雷界面卡一下但状态不变”的伪成功路径

### 3.5 状态采集

任务列表和统计信息并不是从迅雷 UI 读出来的，而是以本地数据库为主、磁盘为辅：

1. agent 读取：
   - `~/Library/Application Support/Thunder/etm3/etm_map.db`

2. 核心数据来自 `etm_task`
   - 任务 id
   - 名称
   - 下载状态
   - 进度
   - 速率
   - 创建/更新时间

3. 再结合本地文件系统补齐文件列表
   - 过滤临时文件
   - 判断文件是否仍然存在
   - 计算单文件大小

4. 生成统一快照
   - `tasks`
   - `stats`
   - `events`
   - `migrations`
   - `pendingDialogs`

## 4. 文件迁移逻辑

下载完成后的媒体迁移由 Mac agent 负责，当前规则如下：

1. 目标服务器通过 SSH / rsync 接收文件
2. 默认迁移目标：
   - `remote_host = 192.168.1.18`
   - `remote_root = /srv/media/zhixingheyi`

3. 自动迁移仅处理单文件任务
   - 如果任务只包含一个有效媒体文件，会在“稳定时间”后自动迁移

4. 多文件任务不自动迁移
   - 页面上会显示“手动迁移”
   - 由用户在任务里选择具体文件迁移

5. 迁移完成后会记录：
   - 迁移状态
   - 迁移时间
   - 远端路径

这样可以避免“一个下载任务里有多个文件时，系统擅自全部迁过去”的问题。

## 5. 前端实现逻辑

前端当前已经全面迁移到 `Vue 3 + Element Plus`，不再直接维护旧的手写 DOM。

当前实现要点：

1. 统一状态入口在：
   - `server/frontend/src/composables/useThunderBridge.js`

2. 页面通过 WebSocket 持续接收快照
   - `/ws`

3. 所有命令都走统一的 `dispatchCommandWithFeedback`
   - 下发命令
   - 等待 `commandId`
   - 进入全局 loading
   - 收到最终事件后再结束 loading

4. 待确认窗口是前端本地浮窗
   - 基于 `pendingDialogs` 渲染
   - 位置与层级在前端本地维护
   - 现在支持拖拽与置顶

5. `server/public/` 不再手改
   - 前端源码维护在 `server/frontend/`
   - 通过 `npm run build` 输出到 `server/public/`

## 6. 当前边界与未完成项

当前已经稳定落地的能力：

- 普通 URL 任务直连创建
- 磁力/BT 文件列表预览
- 页面勾选后开始下载
- 任务开始 / 暂停 / 删除
- 单文件自动迁移
- 多文件手动迁移
- 全局 loading 与命令回执提示

当前仍然保留的边界：

- BT/磁力文件选择仍依赖迅雷预览窗口和辅助功能读取
- 还没有把 BT 子文件选择完全切到纯内部 API
- 不接入 VIP、加速、云下载、离线下载、加速 token 等红线通道

## 7. 相关文件

- `README.md`
- `MAINLINE_TASK.md`
- `docs/THUNDER_CHANNEL_INVENTORY_2026-04-22.md`
- `docs/DEPLOYMENT.md`
- `server/server.js`
- `server/frontend/src/composables/useThunderBridge.js`
- `mac/thunder-agent.py`

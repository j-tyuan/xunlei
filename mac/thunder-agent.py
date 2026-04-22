#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import random
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.parse
from pathlib import Path

APP_DIR = Path.home() / ".thunder-bridge"
CONFIG_PATH = APP_DIR / "config.json"
STATE_PATH = APP_DIR / "state.json"
LOG_PATH = APP_DIR / "agent.log"

DEFAULT_CONFIG = {
    "server_ws": "ws://192.168.1.18:8098/ws",
    "token": "change-me",
    "poll_interval_seconds": 2,
    "stable_seconds": 120,
    "download_root": str(Path.home() / "Downloads"),
    "thunder_db": str(Path.home() / "Library/Application Support/Thunder/etm3/etm_map.db"),
    "migration_enabled": True,
    "migrate_existing_completed": False,
    "auto_confirm_thunder": True,
    "auto_confirm_seconds": 12,
    "remote_host": "192.168.1.18",
    "remote_user": "jiangguiqi",
    "remote_root": "/srv/media/movies",
    "ssh_key": str(Path.home() / ".ssh/thunder_bridge_ed25519"),
}

STATE_LABELS = {
    0: "waiting",
    1: "downloading",
    2: "paused",
    3: "completed",
    4: "failed",
}

TEMP_SUFFIXES = (".xltd", ".td", ".download", ".part", ".aria2", ".tmp")
RSYNC_EXCLUDES = [
    ".DS_Store",
    "*.xltd",
    "*.td",
    "*.download",
    "*.part",
    "*.aria2",
    "*.tmp",
    ".magent_*.torrent",
]


def log(message):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_config():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    config = DEFAULT_CONFIG.copy()
    config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return config


def load_state():
    if not STATE_PATH.exists():
        return {
            "initialized": False,
            "started_at": int(time.time()),
            "skip_completed": [],
            "migrated": {},
            "samples": {},
            "migrations": [],
        }
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {
            "initialized": False,
            "started_at": int(time.time()),
            "skip_completed": [],
            "migrated": {},
            "samples": {},
            "migrations": [],
        }


def save_state(state):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def to_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def parse_json(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def thunder_running():
    result = subprocess.run(
        ["pgrep", "-f", "/Applications/Thunder.app"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def read_tasks(config):
    db_path = config["thunder_db"]
    if not os.path.exists(db_path):
        return []

    query = """
        select taskid,state,create_time,update_time,finish_time,type,create_param,
               sub_task_info,bt_task_info,origin_bytes,server_bytes,p2p_Bytes,dcdn_Bytes
        from etm_task
        where in_rubbish = 0
        order by update_time desc
        limit 100
    """
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(query).fetchall()
    connection.close()

    tasks = []
    for row in rows:
        create_param = parse_json(row["create_param"])
        bt_info = parse_json(row["bt_task_info"])
        sub_info = parse_json(row["sub_task_info"])
        subtasks = bt_info.get("subtask") or sub_info.get("subtask") or []
        selected = [item for item in subtasks if to_int(item.get("is_select"), 1) == 1] or subtasks

        total_size = sum(to_int(item.get("file_size")) for item in selected)
        downloaded = sum(to_int(item.get("download_size")) for item in selected)
        speed = sum(to_int(item.get("download_speed")) for item in selected)
        if total_size <= 0:
            total_size = to_int(create_param.get("file_size"))

        name = create_param.get("file_name") or ""
        if not name and selected:
            name = selected[0].get("etm_file_name") or selected[0].get("file_name") or ""
        if not name:
            name = f"task-{row['taskid']}"

        state_code = to_int(row["state"])
        status = STATE_LABELS.get(state_code, "unknown")
        if speed > 0 and status != "completed":
            status = "downloading"
        if row["finish_time"] and total_size > 0 and downloaded >= total_size:
            status = "completed"
        if status == "completed":
            speed = 0

        source_path = resolve_source_path(config, create_param, selected)
        files = build_task_files(config, create_param, selected, source_path)
        file_count = len(files)
        needs_manual_migration = file_count > 1
        progress = 100 if status == "completed" else 0
        if total_size > 0:
            progress = min(100, downloaded * 100 / total_size)

        tasks.append(
            {
                "id": str(row["taskid"]),
                "name": name,
                "status": status,
                "stateCode": state_code,
                "type": to_int(row["type"]),
                "downloaded": downloaded,
                "totalSize": total_size,
                "speed": speed,
                "progress": progress,
                "path": source_path or str(Path(config["download_root"]) / name),
                "files": files,
                "fileCount": file_count,
                "needsManualMigration": needs_manual_migration,
                "createTime": to_int(row["create_time"]),
                "updateTime": to_int(row["update_time"]),
                "finishTime": to_int(row["finish_time"]),
            }
        )
    return tasks


def resolve_source_path(config, create_param, selected):
    download_path = create_param.get("download_path") or config["download_root"]
    file_name = create_param.get("file_name") or ""
    candidates = []
    if file_name:
        candidates.append(Path(download_path) / file_name)
    for item in selected:
        sub_name = item.get("etm_file_name") or item.get("file_name")
        if not sub_name:
            continue
        if file_name:
            candidates.append(Path(download_path) / file_name / sub_name)
        candidates.append(Path(download_path) / sub_name)
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0]) if candidates else ""


def build_task_files(config, create_param, selected, source_path):
    download_path = create_param.get("download_path") or config["download_root"]
    folder_name = create_param.get("file_name") or ""
    files = []
    seen = set()

    for order, item in enumerate(selected):
        sub_name = item.get("etm_file_name") or item.get("file_name") or ""
        if not sub_name:
            continue
        candidates = []
        if folder_name:
            candidates.append(Path(download_path) / folder_name / sub_name)
        candidates.append(Path(download_path) / sub_name)
        existing = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
        if should_exclude(existing) or str(existing) in seen:
            continue
        seen.add(str(existing))
        files.append(
            {
                "id": str(item.get("index", order)),
                "name": sub_name,
                "path": str(existing),
                "size": to_int(item.get("file_size")) or path_size(str(existing)),
                "downloaded": to_int(item.get("download_size")),
                "status": STATE_LABELS.get(to_int(item.get("state")), "unknown"),
                "exists": existing.exists(),
            }
        )

    if files:
        return files

    source = Path(source_path) if source_path else Path(download_path) / folder_name
    if source.is_file() and not should_exclude(source):
        return [
            {
                "id": "0",
                "name": source.name,
                "path": str(source),
                "size": path_size(str(source)),
                "downloaded": path_size(str(source)),
                "status": "completed",
                "exists": True,
            }
        ]
    if source.is_dir():
        for file_path in enumerate_download_files(source):
            files.append(
                {
                    "id": str(len(files)),
                    "name": file_path.name,
                    "path": str(file_path),
                    "size": path_size(str(file_path)),
                    "downloaded": path_size(str(file_path)),
                    "status": "completed",
                    "exists": True,
                }
            )
    return files


def enumerate_download_files(root):
    result = []
    for current_root, _, filenames in os.walk(root):
        for filename in filenames:
            file_path = Path(current_root) / filename
            if should_exclude(file_path):
                continue
            result.append(file_path)
    return sorted(result, key=lambda item: str(item).lower())


def path_size(path):
    target = Path(path)
    if not target.exists():
        return 0
    if target.is_file():
        return target.stat().st_size
    total = 0
    for root, _, files in os.walk(target):
        for filename in files:
            item = Path(root) / filename
            if should_exclude(item):
                continue
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def latest_mtime(path):
    target = Path(path)
    if not target.exists():
        return 0
    if target.is_file():
        return target.stat().st_mtime
    latest = target.stat().st_mtime
    for root, _, files in os.walk(target):
        for filename in files:
            item = Path(root) / filename
            if should_exclude(item):
                continue
            try:
                latest = max(latest, item.stat().st_mtime)
            except OSError:
                pass
    return latest


def should_exclude(path):
    name = path.name
    if name == ".DS_Store":
        return True
    if name.startswith(".magent_") and name.endswith(".torrent"):
        return True
    return name.endswith(TEMP_SUFFIXES)


def has_temp_files(path):
    target = Path(path)
    if not target.exists():
        return False
    if target.is_file():
        return target.name.endswith(TEMP_SUFFIXES)
    for root, _, files in os.walk(target):
        for filename in files:
            if filename.endswith(TEMP_SUFFIXES):
                return True
    return False


def stable_enough(task, state, config):
    path = task.get("path")
    if not path or not Path(path).exists():
        return False, "source missing"
    if has_temp_files(path):
        return False, "temporary files still exist"
    size = path_size(path)
    if size <= 0:
        return False, "empty source"

    now = int(time.time())
    mtime = latest_mtime(path)
    if now - mtime < int(config["stable_seconds"]):
        return False, "recently modified"

    samples = state.setdefault("samples", {})
    sample = samples.get(task["id"])
    if not sample or sample.get("size") != size:
        samples[task["id"]] = {"size": size, "since": now}
        return False, "waiting for stable size"
    if now - int(sample.get("since", now)) < int(config["stable_seconds"]):
        return False, "confirming stable size"
    return True, "stable"


def shell_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def ensure_remote(config):
    key = config["ssh_key"]
    remote = f"{config['remote_user']}@{config['remote_host']}"
    command = f"mkdir -p {shell_quote(config['remote_root'])} && chmod -R ug+rwX {shell_quote(config['remote_root'])}"
    subprocess.run(
        ["ssh", "-i", key, "-o", "StrictHostKeyChecking=no", remote, command],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def migrate_task(task, state, config):
    source = Path(task["path"])
    if not source.exists():
        raise RuntimeError("source missing")
    remote_path = migrate_source(source, config)

    migrated = {
        "name": task["name"],
        "status": "done",
        "message": f"moved to {remote_path}",
        "time": int(time.time()),
    }
    state.setdefault("migrated", {})[task["id"]] = migrated
    state.setdefault("migrations", []).insert(0, migrated)
    state["migrations"] = state["migrations"][:20]
    log(f"migrated task {task['id']} {task['name']} -> {remote_path}")


def migrate_source(source, config):
    ensure_remote(config)
    key = config["ssh_key"]
    remote = f"{config['remote_user']}@{config['remote_host']}"
    destination = f"{remote}:{config['remote_root'].rstrip('/')}/"
    command = ["rsync", "-a", "--partial"]
    for pattern in RSYNC_EXCLUDES:
        command.append(f"--exclude={pattern}")
    command.extend(["-e", f"ssh -i {key} -o StrictHostKeyChecking=no", str(source), destination])
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    remote_path = f"{config['remote_root'].rstrip('/')}/{source.name}"
    verify = f"test -e {shell_quote(remote_path)}"
    subprocess.run(
        ["ssh", "-i", key, "-o", "StrictHostKeyChecking=no", remote, verify],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if source.is_dir():
        shutil.rmtree(source)
    else:
        source.unlink()
    return remote_path


def is_safe_download_child(path, config):
    root = Path(config["download_root"]).expanduser().resolve()
    target = Path(path).expanduser().resolve()
    if root == Path.home().resolve() or str(root) in ("/", ""):
        return False
    return str(target).startswith(str(root) + os.sep)


def migrate_selected_file(file_path, state, config, display_name=""):
    if not is_safe_download_child(file_path, config):
        raise RuntimeError("refuse to migrate path outside download root")
    source = Path(file_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise RuntimeError("selected file not found")
    if should_exclude(source):
        raise RuntimeError("selected file is a temporary/control file")
    remote_path = migrate_source(source, config)
    record = {
        "name": display_name or source.name,
        "status": "done",
        "message": f"manually moved to {remote_path}",
        "time": int(time.time()),
    }
    state.setdefault("migrations", []).insert(0, record)
    state["migrations"] = state["migrations"][:20]
    log(f"manually migrated {source} -> {remote_path}")
    return remote_path


def cleanup_download_dir(config, confirm):
    if confirm != "清空":
        raise RuntimeError("cleanup confirmation mismatch")
    root = Path(config["download_root"]).expanduser().resolve()
    home = Path.home().resolve()
    if root in (Path("/"), home) or not str(root).startswith(str(home) + os.sep):
        raise RuntimeError(f"refuse to cleanup unsafe directory: {root}")
    if not root.exists() or not root.is_dir():
        raise RuntimeError("download directory does not exist")

    removed = 0
    bytes_removed = 0
    for child in root.iterdir():
        try:
            bytes_removed += path_size(str(child))
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
        except FileNotFoundError:
            pass
    log(f"cleaned download dir {root}, entries={removed}, bytes={bytes_removed}")
    return {"root": str(root), "removed": removed, "bytes": bytes_removed}


def handle_migrations(tasks, state, config):
    if not config.get("migration_enabled", True):
        return
    completed_ids = [task["id"] for task in tasks if task["status"] == "completed"]
    if not state.get("initialized"):
        state["initialized"] = True
        state["started_at"] = int(time.time())
        if not config.get("migrate_existing_completed", False):
            state["skip_completed"] = sorted(set(state.get("skip_completed", []) + completed_ids))
        save_state(state)
        return

    skip_completed = set(state.get("skip_completed", []))
    migrated = state.get("migrated", {})
    for task in tasks:
        if task["status"] != "completed":
            continue
        if task["id"] in skip_completed or task["id"] in migrated:
            continue
        if task.get("needsManualMigration"):
            remember_migration_status(state, task, "manual-required", "包含多个文件，请在页面选择要迁移的文件")
            continue
        ok, reason = stable_enough(task, state, config)
        if not ok:
            remember_migration_status(state, task, "waiting", reason)
            continue
        try:
            remember_migration_status(state, task, "moving", "rsync in progress")
            save_state(state)
            migrate_task(task, state, config)
        except Exception as error:
            remember_migration_status(state, task, "failed", str(error))
            log(f"migration failed for task {task['id']}: {error}")


def remember_migration_status(state, task, status, message):
    records = state.setdefault("migrations", [])
    record = {
        "name": task["name"],
        "status": status,
        "message": message,
        "time": int(time.time()),
    }
    records = [item for item in records if item.get("name") != task["name"]]
    records.insert(0, record)
    state["migrations"] = records[:20]


def is_supported_download_url(value):
    value = (value or "").strip().lower()
    return value.startswith(
        ("magnet:?", "http://", "https://", "ftp://", "ed2k://", "thunder://")
    )


def add_task_to_thunder(url, config=None):
    if not is_supported_download_url(url):
        raise RuntimeError("unsupported download url")

    # Thunder registers URL handlers on macOS; opening the URL with Thunder is
    # more stable than trying to reverse-engineer its private download service.
    thunder_open = subprocess.run(
        ["open", "-a", "Thunder", url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if thunder_open.returncode != 0:
        raise RuntimeError((thunder_open.stderr or thunder_open.stdout or "failed to open Thunder").strip())

    config = config or {}
    if config.get("auto_confirm_thunder", True):
        confirm_thunder_download(float(config.get("auto_confirm_seconds", 12)))


def confirm_thunder_download(timeout_seconds=12):
    script = f"""
on buttonMatches(buttonName)
  if buttonName contains "立即下载" then return true
  if buttonName contains "开始下载" then return true
  if buttonName is "下载" then return true
  if buttonName contains "确定" then return true
  if buttonName contains "添加" then return true
  return false
end buttonMatches

tell application "Thunder" to activate
delay 0.6
tell application "System Events"
  tell process "Thunder"
    set frontmost to true
    repeat with i from 1 to {int(timeout_seconds * 2)}
      repeat with w in windows
        repeat with b in buttons of w
          try
            set buttonName to name of b as text
            if my buttonMatches(buttonName) then
              click b
              return "clicked:" & buttonName
            end if
          end try
        end repeat
      end repeat
      delay 0.5
    end repeat
    key code 36
    return "pressed:return"
  end tell
end tell
"""
    result = subprocess.run(
        ["osascript", "-e", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown accessibility error").strip()
        raise RuntimeError(f"已打开迅雷新建任务窗口，但自动点击立即下载失败：{detail}")
    log(f"thunder confirm result: {(result.stdout or '').strip()}")


def send_agent_event(client, config, event):
    client.send_json({"role": "agent", "token": config["token"], "event": event})


def handle_commands(client, config):
    for message in client.receive_json():
        if message.get("type") != "command":
            continue
        command = message.get("command")
        command_id = message.get("id")
        try:
            if command == "addTask":
                url = (message.get("url") or "").strip()
                add_task_to_thunder(url, config)
                log(f"add task command accepted: {url[:120]}")
                send_agent_event(
                    client,
                    config,
                    {
                        "kind": "command",
                        "status": "accepted",
                        "title": "Mac 迅雷已接收任务",
                        "message": url[:160],
                        "commandId": command_id,
                    },
                )
            elif command == "migrateFile":
                state = load_state()
                file_path = (message.get("filePath") or "").strip()
                display_name = (message.get("name") or "").strip()
                remote_path = migrate_selected_file(file_path, state, config, display_name)
                save_state(state)
                send_agent_event(
                    client,
                    config,
                    {
                        "kind": "migration",
                        "status": "done",
                        "title": "手动迁移完成",
                        "message": remote_path,
                        "commandId": command_id,
                    },
                )
            elif command == "cleanupDownloadDir":
                result = cleanup_download_dir(config, message.get("confirm"))
                send_agent_event(
                    client,
                    config,
                    {
                        "kind": "cleanup",
                        "status": "done",
                        "title": "迅雷下载目录已清空",
                        "message": f"{result['root']}，删除 {result['removed']} 项，释放 {result['bytes']} 字节",
                        "commandId": command_id,
                    },
                )
            else:
                continue
        except Exception as error:
            log(f"{command} command failed: {error}")
            send_agent_event(
                client,
                config,
                {
                    "kind": "command",
                    "status": "failed",
                    "title": "Mac 命令执行失败",
                    "message": str(error),
                    "commandId": command_id,
                },
            )


class WebSocketClient:
    def __init__(self, url):
        self.url = url
        self.sock = None
        self.buffer = b""

    def connect(self):
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme != "ws":
            raise RuntimeError("only ws:// is supported")
        host = parsed.hostname
        port = parsed.port or 80
        path = parsed.path or "/"
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        sock = socket.create_connection((host, port), timeout=5)
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("websocket upgrade failed")
        sock.settimeout(0.05)
        self.sock = sock
        self.buffer = b""

    def send_json(self, data):
        if not self.sock:
            self.connect()
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(length.to_bytes(2, "big"))
        else:
            header.append(0x80 | 127)
            header.extend(length.to_bytes(8, "big"))
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def receive_json(self):
        if not self.sock:
            self.connect()
        while True:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                raise RuntimeError("websocket closed")
            self.buffer += chunk

        messages = []
        offset = 0
        while offset + 2 <= len(self.buffer):
            first = self.buffer[offset]
            second = self.buffer[offset + 1]
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            header_len = 2
            if length == 126:
                if offset + 4 > len(self.buffer):
                    break
                length = int.from_bytes(self.buffer[offset + 2 : offset + 4], "big")
                header_len = 4
            elif length == 127:
                if offset + 10 > len(self.buffer):
                    break
                length = int.from_bytes(self.buffer[offset + 2 : offset + 10], "big")
                header_len = 10
            mask_len = 4 if masked else 0
            frame_len = header_len + mask_len + length
            if offset + frame_len > len(self.buffer):
                break
            payload = self.buffer[offset + header_len + mask_len : offset + frame_len]
            if masked:
                mask = self.buffer[offset + header_len : offset + header_len + 4]
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x1:
                messages.append(json.loads(payload.decode("utf-8")))
            elif opcode == 0x8:
                raise RuntimeError("websocket closed by server")
            offset += frame_len
        self.buffer = self.buffer[offset:]
        return messages

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        self.buffer = b""


def build_snapshot(tasks, state):
    total_speed = sum(task.get("speed", 0) for task in tasks)
    active = sum(1 for task in tasks if task.get("status") == "downloading")
    completed = sum(1 for task in tasks if task.get("status") == "completed")
    return {
        "mac": {
            "host": socket.gethostname(),
            "user": os.environ.get("USER", ""),
            "thunderRunning": thunder_running(),
            "collectedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
        "stats": {
            "totalSpeed": total_speed,
            "activeTasks": active,
            "completedTasks": completed,
            "totalTasks": len(tasks),
        },
        "tasks": tasks,
        "migrations": state.get("migrations", [])[:20],
    }


def main():
    config = load_config()
    state = load_state()
    client = WebSocketClient(config["server_ws"])
    log("thunder agent started")
    while True:
        try:
            tasks = read_tasks(config)
            handle_migrations(tasks, state, config)
            save_state(state)
            snapshot = build_snapshot(tasks, state)
            client.send_json({"role": "agent", "token": config["token"], "payload": snapshot})
            handle_commands(client, config)
        except Exception as error:
            log(f"loop error: {error}")
            client.close()
            time.sleep(3)
        time.sleep(float(config["poll_interval_seconds"]))


if __name__ == "__main__":
    main()

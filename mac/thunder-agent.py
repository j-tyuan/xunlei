#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import pwd
import random
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import traceback
import urllib.parse
from pathlib import Path

APP_DIR = Path.home() / ".thunder-bridge"
CONFIG_PATH = APP_DIR / "config.json"
STATE_PATH = APP_DIR / "state.json"
LOG_PATH = APP_DIR / "agent.log"
DOWNLOAD_SERVICE_PATH = "/Applications/Thunder.app/Contents/XPCServices/DownloadService.xpc/Contents/MacOS/DownloadService"

DEFAULT_CONFIG = {
    "server_ws": "ws://192.168.1.18:8098/ws",
    "token": "change-me",
    "poll_interval_seconds": 2,
    "idle_status_poll_interval_seconds": 2,
    "busy_status_poll_interval_seconds": 0.75,
    "interactive_status_poll_interval_seconds": 0.35,
    "command_poll_interval_seconds": 0.12,
    "stable_seconds": 120,
    "download_root": str(Path.home() / "Downloads"),
    "thunder_db": str(Path.home() / "Library/Application Support/Thunder/etm3/etm_map.db"),
    "migration_enabled": True,
    "auto_migration_enabled": True,
    "migrate_existing_completed": False,
    "task_create_mode": "direct",
    "direct_create_timeout_seconds": 20,
    "direct_action_timeout_seconds": 14,
    "direct_create_fallback_to_ui": True,
    "auto_confirm_thunder": True,
    "auto_confirm_seconds": 12,
    "remote_host": "192.168.1.18",
    "remote_user": "jiangguiqi",
    "remote_root": "/srv/media/zhixingheyi",
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

MIGRATION_LOCK = threading.Lock()
MIGRATION_RUNTIME = {
    "thread": None,
    "taskId": "",
    "taskName": "",
    "result": None,
}
ACCESSIBILITY_RUNTIME = {
    "prefer_localhost_ssh": False,
}


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
    default_state = {
        "initialized": False,
        "started_at": int(time.time()),
        "skip_completed": [],
        "migrated": {},
        "samples": {},
        "migrations": [],
        "pendingDialogs": [],
        "pendingDialog": None,
    }
    if not STATE_PATH.exists():
        return default_state
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        for key, value in default_state.items():
            state.setdefault(key, value)
        return state
    except Exception:
        return default_state


def save_state(state):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def migration_task_id():
    with MIGRATION_LOCK:
        thread = MIGRATION_RUNTIME.get("thread")
        if thread and thread.is_alive():
            return MIGRATION_RUNTIME.get("taskId", "")
        return ""


def begin_background_migration(task, config):
    with MIGRATION_LOCK:
        thread = MIGRATION_RUNTIME.get("thread")
        if thread and thread.is_alive():
            return False

        task_payload = {
            "id": str(task.get("id", "")),
            "name": task.get("name", ""),
            "path": task.get("path", ""),
        }
        config_payload = dict(config)
        MIGRATION_RUNTIME["thread"] = threading.Thread(
            target=run_background_migration,
            args=(task_payload, config_payload),
            daemon=True,
        )
        MIGRATION_RUNTIME["taskId"] = task_payload["id"]
        MIGRATION_RUNTIME["taskName"] = task_payload["name"]
        MIGRATION_RUNTIME["result"] = None
        MIGRATION_RUNTIME["thread"].start()
        return True


def run_background_migration(task, config):
    try:
        result = migrate_task(task, config)
    except Exception as error:
        result = migration_record(task.get("name", ""), "failed", str(error), task.get("id", ""))
        log(f"migration failed for task {task.get('id', '')}: {error}")
    with MIGRATION_LOCK:
        MIGRATION_RUNTIME["result"] = result


def drain_background_migration(state):
    with MIGRATION_LOCK:
        result = MIGRATION_RUNTIME.get("result")
        thread = MIGRATION_RUNTIME.get("thread")
        if not result:
            if thread and not thread.is_alive():
                MIGRATION_RUNTIME["thread"] = None
                MIGRATION_RUNTIME["taskId"] = ""
                MIGRATION_RUNTIME["taskName"] = ""
            return False
        MIGRATION_RUNTIME["result"] = None
        MIGRATION_RUNTIME["thread"] = None
        MIGRATION_RUNTIME["taskId"] = ""
        MIGRATION_RUNTIME["taskName"] = ""

    remember_migration_record(
        state,
        result.get("name", ""),
        result.get("status", "unknown"),
        result.get("message", ""),
        result.get("taskId", ""),
        result.get("time"),
    )
    if result.get("status") == "done" and result.get("taskId"):
        state.setdefault("migrated", {})[result["taskId"]] = {
            "name": result.get("name", ""),
            "status": result.get("status", "done"),
            "message": result.get("message", ""),
            "time": result.get("time", int(time.time())),
        }
    return True


def get_pending_dialogs(state):
    dialogs = state.get("pendingDialogs")
    if isinstance(dialogs, list):
        return dialogs
    legacy = state.get("pendingDialog")
    return [legacy] if legacy else []


def set_pending_dialogs(state, dialogs):
    normalized = [item for item in (dialogs or []) if item and item.get("id")]
    state["pendingDialogs"] = normalized
    state["pendingDialog"] = normalized[0] if normalized else None


def to_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
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
    return thunder_pid() is not None


def thunder_pid():
    result = subprocess.run(
        ["pgrep", "-f", "/Applications/Thunder.app/Contents/MacOS/Thunder"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        value = line.strip()
        if value.isdigit():
            return value
    return None


def download_service_pid():
    result = subprocess.run(
        ["pgrep", "-f", DOWNLOAD_SERVICE_PATH],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        value = line.strip()
        if value.isdigit():
            return value
    return None


def wait_for_download_service(timeout_seconds=15):
    wait_for_thunder(timeout_seconds)
    deadline = time.time() + max(1, float(timeout_seconds))
    while time.time() < deadline:
        pid = download_service_pid()
        if pid:
            return pid
        time.sleep(0.35)
    raise RuntimeError("DownloadService did not start in time")


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
        if not existing.exists() or should_exclude(existing) or str(existing) in seen:
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


def migration_record(name, status, message, task_id="", at_time=None):
    record = {
        "name": name,
        "status": status,
        "message": message,
        "time": int(at_time or time.time()),
    }
    if task_id:
        record["taskId"] = str(task_id)
    return record


def migrate_task(task, config):
    source = Path(task["path"])
    if not source.exists():
        raise RuntimeError("source missing")
    remote_path = migrate_source(source, config)
    log(f"migrated task {task['id']} {task['name']} -> {remote_path}")
    return migration_record(task["name"], "done", f"moved to {remote_path}", task["id"])


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
    remember_migration_record(
        state,
        display_name or source.name,
        "done",
        f"manually moved to {remote_path}",
    )
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


def delete_selected_file(file_path, config, display_name=""):
    if not is_safe_download_child(file_path, config):
        raise RuntimeError("refuse to delete path outside download root")
    source = Path(file_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise RuntimeError("selected file not found")
    if should_exclude(source):
        raise RuntimeError("selected file is a temporary/control file")
    size = path_size(str(source))
    source.unlink()
    log(f"deleted local file {source}, bytes={size}")
    return {"name": display_name or source.name, "path": str(source), "bytes": size}


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
    active_task_id = migration_task_id()
    for task in tasks:
        if task["status"] != "completed":
            continue
        if task["id"] in skip_completed or task["id"] in migrated:
            continue
        if active_task_id and task["id"] == active_task_id:
            remember_migration_status(state, task, "moving", "rsync in background")
            continue
        if task.get("needsManualMigration"):
            remember_migration_status(state, task, "manual-required", "包含多个文件，请在页面选择要迁移的文件")
            continue
        ok, reason = stable_enough(task, state, config)
        if not ok:
            remember_migration_status(state, task, "waiting", reason)
            continue
        if active_task_id:
            continue
        try:
            remember_migration_status(state, task, "moving", "rsync in background")
            if begin_background_migration(task, config):
                log(f"queued background migration for task {task['id']} {task['name']}")
                active_task_id = task["id"]
            else:
                remember_migration_status(state, task, "waiting", "migration worker busy")
        except Exception as error:
            remember_migration_status(state, task, "failed", str(error))
            log(f"migration failed for task {task['id']}: {error}")


def remember_migration_record(state, name, status, message, task_id="", at_time=None):
    record = migration_record(name, status, message, task_id, at_time)
    records = state.setdefault("migrations", [])
    filtered = []
    for item in records:
        same_task = task_id and item.get("taskId") == task_id
        same_name = name and item.get("name") == name
        if same_task or same_name:
            continue
        filtered.append(item)
    filtered.insert(0, record)
    state["migrations"] = filtered[:20]


def remember_migration_status(state, task, status, message):
    remember_migration_record(state, task["name"], status, message, task.get("id", ""))


def is_supported_download_url(value):
    value = (value or "").strip().lower()
    return value.startswith(
        ("magnet:?", "http://", "https://", "ftp://", "ed2k://", "thunder://")
    )


def download_url_scheme(value):
    parsed = urllib.parse.urlparse((value or "").strip())
    return (parsed.scheme or "").lower()


def can_use_direct_create(url):
    return download_url_scheme(url) in ("http", "https", "ftp")


def requires_task_preview(url):
    return download_url_scheme(url) in ("magnet", "ed2k", "thunder")


def current_username():
    return os.environ.get("USER") or os.environ.get("LOGNAME") or pwd.getpwuid(os.getuid()).pw_name


def ensure_local_ssh_key(config):
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ssh_dir, 0o700)
    except OSError:
        pass

    key_path = Path(config.get("ssh_key") or (ssh_dir / "thunder_bridge_ed25519")).expanduser()
    pub_path = Path(f"{key_path}.pub")
    if not key_path.exists() or not pub_path.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    authorized_keys = ssh_dir / "authorized_keys"
    authorized_keys.touch(exist_ok=True)
    try:
        os.chmod(authorized_keys, 0o600)
    except OSError:
        pass

    public_key = pub_path.read_text(encoding="utf-8").strip()
    existing_lines = authorized_keys.read_text(encoding="utf-8").splitlines()
    if public_key and public_key not in existing_lines:
        with authorized_keys.open("a", encoding="utf-8") as handle:
            handle.write(public_key + "\n")
    return key_path


def wait_for_thunder(timeout_seconds=15):
    pid = thunder_pid()
    if pid:
        return pid

    result = subprocess.run(
        ["open", "-a", "Thunder"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "failed to start Thunder").strip())

    deadline = time.time() + max(1, float(timeout_seconds))
    while time.time() < deadline:
        pid = thunder_pid()
        if pid:
            return pid
        time.sleep(0.5)
    raise RuntimeError("Thunder did not start in time")


def infer_task_file_name(url, preferred_name=""):
    if preferred_name:
        return preferred_name.strip()

    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme == "magnet":
        name = urllib.parse.parse_qs(parsed.query).get("dn", [""])[0]
    else:
        name = Path(urllib.parse.unquote(parsed.path or "")).name
    return name.replace("/", "_").replace("\\", "_").strip()


def lldb_string_literal(value):
    return '@"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def task_exists_for_url(config, url, min_created_at=0):
    db_path = config["thunder_db"]
    if not os.path.exists(db_path):
        return False

    query = """
        select taskid
        from etm_task
        where instr(create_param, ?) > 0
          and create_time >= ?
        order by taskid desc
        limit 1
    """
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
    row = connection.execute(query, (url, int(min_created_at))).fetchone()
    connection.close()
    return row is not None


def find_task_by_id(config, task_id):
    wanted = str(task_id or "").strip()
    if not wanted:
        return None
    for task in read_tasks(config):
        if str(task.get("id", "")) == wanted:
            return task
    return None


def run_lldb_script(pid, script_text, timeout_seconds):
    script_file = tempfile.NamedTemporaryFile("w", suffix=".lldb", delete=False, encoding="utf-8")
    try:
        script_file.write(script_text)
        script_file.close()
        return subprocess.run(
            ["lldb", "-p", str(pid), "-b", "-s", script_file.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(5, float(timeout_seconds)),
        )
    finally:
        try:
            os.unlink(script_file.name)
        except OSError:
            pass


def build_direct_create_lldb_script(payload_path, started_path, returned_path, callback_path):
    expression = " ".join(
        [
            "({",
            f"NSString *codexPayloadPath = {lldb_string_literal(payload_path)};",
            f"NSString *codexStartedPath = {lldb_string_literal(started_path)};",
            f"NSString *codexReturnedPath = {lldb_string_literal(returned_path)};",
            f"NSString *codexCallbackPath = {lldb_string_literal(callback_path)};",
            "NSData *codexPayloadData = [NSData dataWithContentsOfFile:codexPayloadPath];",
            "NSDictionary *codexPayload = (NSDictionary *)[NSJSONSerialization JSONObjectWithData:codexPayloadData options:0 error:nil];",
            "id codexHostObj = (id)[(id)NSClassFromString(@\"Thunder.BaseHostController\") new];",
            "CFRetain((__bridge CFTypeRef)codexHostObj);",
            "NSMutableDictionary *codexTask = [NSMutableDictionary dictionary];",
            "[codexTask setObject:([codexPayload objectForKey:@\"url\"] ?: @\"\") forKey:(id)DSKeyCreateTaskURL];",
            "[codexTask setObject:([codexPayload objectForKey:@\"fileName\"] ?: @\"\") forKey:(id)DSKeyCreateTaskFileName];",
            "[codexTask setObject:([codexPayload objectForKey:@\"saveDirPath\"] ?: @\"\") forKey:(id)DSKeyCreateTaskFilePath];",
            "[codexTask setObject:@0 forKey:(id)DSKeyCreateTaskFileSize];",
            "[codexTask setObject:@\"codex-thunder-bridge/direct\" forKey:@\"source\"];",
            "NSDictionary *codexTaskCopy = [codexTask copy];",
            "CFRetain((__bridge CFTypeRef)codexTaskCopy);",
            "dispatch_async(dispatch_get_main_queue(), ^{",
            "[@\"started\" writeToFile:codexStartedPath atomically:YES encoding:NSUTF8StringEncoding error:nil];",
            "void (^codexCompletionBlock)(void) = ^{",
            "[@\"callback\" writeToFile:codexCallbackPath atomically:YES encoding:NSUTF8StringEncoding error:nil];",
            "};",
            "[codexHostObj createTask:codexTaskCopy completion:codexCompletionBlock];",
            "[@\"returned\" writeToFile:codexReturnedPath atomically:YES encoding:NSUTF8StringEncoding error:nil];",
            "});",
            "[NSString stringWithFormat:@\"scheduled direct create %@\", codexTaskCopy];",
            "})",
        ]
    )
    return "\n".join(
        [
            "settings set interpreter.stop-command-source-on-error false",
            "expr -l objc++ -O -- @import Foundation",
            "expr -l objc++ -O -- @import ObjectiveC",
            f"expr -l objc++ -O -- {expression}",
            "quit",
            "",
        ]
    )


def build_direct_task_action_lldb_script(task_id, action, delete_files=False):
    task_number = to_int(task_id, default=-1)
    if task_number < 0:
        raise RuntimeError("task id is not numeric")

    if action == "start":
        function_decl = "extern int etm_start_task(unsigned long long *, unsigned int, int);"
        invoke = "int codexRc = etm_start_task(codexIds, 1, 0);"
    elif action == "pause":
        function_decl = "extern int etm_stop_task(unsigned long long *, unsigned int, int);"
        invoke = "int codexRc = etm_stop_task(codexIds, 1, 1);"
    elif action == "delete" and delete_files:
        function_decl = "extern int etm_destroy_task(unsigned long long *, unsigned int, int);"
        invoke = "int codexRc = etm_destroy_task(codexIds, 1, 1);"
    elif action == "delete":
        function_decl = "extern int etm_delete_task(unsigned long long *, unsigned int, int);"
        invoke = "int codexRc = etm_delete_task(codexIds, 1, 1);"
    else:
        raise RuntimeError("unsupported task action")

    expression = " ".join(
        [
            "({",
            function_decl,
            f"unsigned long long codexIds[1] = {{ {task_number}ULL }};",
            invoke,
            f'[NSString stringWithFormat:@"codex-xpc-action:{action}:%d", codexRc];',
            "})",
        ]
    )
    return "\n".join(
        [
            "settings set interpreter.stop-command-source-on-error false",
            "expr -l objc++ -O -- @import Foundation",
            f"expr -l objc++ -O -- {expression}",
            "quit",
            "",
        ]
    )


def wait_for_task_action_result(config, task_id, action, previous_status="", timeout_seconds=12):
    deadline = time.time() + max(3, float(timeout_seconds))
    last_status = previous_status or "unknown"
    while time.time() < deadline:
        task = find_task_by_id(config, task_id)
        if action == "delete":
            if not task:
                return {"status": "deleted", "detail": "task-removed"}
        else:
            if task:
                last_status = task.get("status", "unknown")
                if action == "pause" and last_status == "paused":
                    return {"status": last_status, "detail": "paused"}
                if action == "start" and last_status in ("waiting", "downloading", "completed"):
                    return {"status": last_status, "detail": last_status}
                if action == "start" and previous_status == "failed" and last_status != "failed":
                    return {"status": last_status, "detail": last_status}
            else:
                last_status = "missing"
        time.sleep(0.35)
    raise RuntimeError(f"task {action} timed out, last status: {last_status}")


def control_task(task_id, action, config, delete_files=False):
    task_id = str(task_id or "").strip()
    action = str(action or "").strip().lower()
    if not task_id:
        raise RuntimeError("missing task id")
    if action not in ("start", "pause", "delete"):
        raise RuntimeError("unsupported task action")

    current = find_task_by_id(config, task_id)
    if not current:
        raise RuntimeError("task not found")
    if action == "pause" and current.get("status") == "paused":
        return {"task": current, "status": "paused", "detail": "already-paused", "deleteFiles": False}
    if action == "start" and current.get("status") in ("waiting", "downloading"):
        return {"task": current, "status": current.get("status"), "detail": "already-running", "deleteFiles": False}
    if action == "delete" and migration_task_id() == task_id:
        raise RuntimeError("task is currently being migrated")

    pid = wait_for_download_service()
    timeout_seconds = float(config.get("direct_action_timeout_seconds", 14))
    result = run_lldb_script(
        pid,
        build_direct_task_action_lldb_script(task_id, action, delete_files),
        timeout_seconds,
    )
    output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    expected_prefix = f"codex-xpc-action:{action}:"
    if expected_prefix not in output:
        raise RuntimeError(output or "xpc task action did not run")

    outcome = wait_for_task_action_result(config, task_id, action, current.get("status", ""), timeout_seconds)
    detail = outcome.get("detail", action)
    log(f"task action {action} applied to {task_id}: {detail}")
    return {
        "task": current,
        "status": outcome.get("status", "unknown"),
        "detail": detail,
        "deleteFiles": bool(delete_files),
    }


def add_task_to_thunder_direct(url, config, preferred_name=""):
    pid = wait_for_thunder()
    request_id = f"{int(time.time())}-{os.getpid()}-{random.randint(1000, 9999)}"
    started_at = int(time.time()) - 5
    file_name = infer_task_file_name(url, preferred_name)
    save_dir = str(Path(config.get("download_root") or (Path.home() / "Downloads")).expanduser())
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    payload = {
        "url": url,
        "fileName": file_name,
        "saveDirPath": save_dir,
    }
    started_marker = Path(tempfile.gettempdir()) / f"thunder-direct-started-{request_id}.txt"
    returned_marker = Path(tempfile.gettempdir()) / f"thunder-direct-returned-{request_id}.txt"
    callback_marker = Path(tempfile.gettempdir()) / f"thunder-direct-callback-{request_id}.txt"

    payload_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(payload, payload_file, ensure_ascii=False)
        payload_file.close()

        timeout_seconds = float(config.get("direct_create_timeout_seconds", 20))
        result = run_lldb_script(
            pid,
            build_direct_create_lldb_script(
                payload_file.name,
                str(started_marker),
                str(returned_marker),
                str(callback_marker),
            ),
            timeout_seconds,
        )
        output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
        if "scheduled direct create" not in output:
            raise RuntimeError(output or "direct create was not scheduled")

        deadline = time.time() + max(5, timeout_seconds)
        while time.time() < deadline:
            if callback_marker.exists():
                return {"mode": "direct", "detail": "callback"}
            if returned_marker.exists() and task_exists_for_url(config, url, started_at):
                return {"mode": "direct", "detail": "task-row"}
            time.sleep(0.4)

        if task_exists_for_url(config, url, started_at):
            return {"mode": "direct", "detail": "task-row"}
        raise RuntimeError("direct create timed out before callback or task row appeared")
    finally:
        for temporary_path in [
            payload_file.name,
        ]:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def add_task_to_thunder_via_open(url, config):
    if not is_supported_download_url(url):
        raise RuntimeError("unsupported download url")

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
        confirm_thunder_download(float(config.get("auto_confirm_seconds", 12)), config)
    return {"mode": "open-url", "detail": "ui-confirm"}


def add_task_to_thunder(url, config=None, preferred_name=""):
    if not is_supported_download_url(url):
        raise RuntimeError("unsupported download url")

    config = config or {}
    mode = str(config.get("task_create_mode") or "direct").strip().lower()
    if mode == "direct" and can_use_direct_create(url):
        try:
            return add_task_to_thunder_direct(url, config, preferred_name)
        except Exception as error:
            log(f"direct create failed for {url[:120]}: {error}")
            if not config.get("direct_create_fallback_to_ui", True):
                raise
    elif mode == "direct":
        scheme = download_url_scheme(url) or "unknown"
        log(f"skip direct create for {scheme} task, fallback to open-url: {url[:120]}")
    return add_task_to_thunder_via_open(url, config)


def run_osascript(script, timeout_seconds):
    return subprocess.run(
        ["osascript", "-e", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=max(10, int(timeout_seconds) + 4),
    )


def run_osascript_via_local_ssh(script, config, timeout_seconds):
    key_path = ensure_local_ssh_key(config or {})
    script_path = Path(tempfile.gettempdir()) / f"thunder-confirm-{int(time.time())}-{os.getpid()}.applescript"
    script_path.write_text(script, encoding="utf-8")
    try:
        return subprocess.run(
            [
                "ssh",
                "-i",
                str(key_path),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=5",
                f"{current_username()}@127.0.0.1",
                "/usr/bin/osascript",
                str(script_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(12, int(timeout_seconds) + 6),
        )
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass


def accessibility_denied(detail):
    message = (detail or "").lower()
    return "-25211" in message or "不允许辅助访问" in message or "assistive access" in message


def run_accessibility_script(script, timeout_seconds=12, config=None):
    if ACCESSIBILITY_RUNTIME.get("prefer_localhost_ssh"):
        ssh_result = run_osascript_via_local_ssh(script, config or {}, timeout_seconds)
        if ssh_result.returncode == 0:
            return ssh_result, "localhost-ssh"
        ACCESSIBILITY_RUNTIME["prefer_localhost_ssh"] = False

    result = run_osascript(script, timeout_seconds)
    if result.returncode == 0:
        ACCESSIBILITY_RUNTIME["prefer_localhost_ssh"] = False
        return result, "direct"

    detail = (result.stderr or result.stdout or "").strip()
    if accessibility_denied(detail):
        log("osascript accessibility denied in launchd context, retry via localhost ssh")
        ssh_result = run_osascript_via_local_ssh(script, config or {}, timeout_seconds)
        if ssh_result.returncode == 0:
            ACCESSIBILITY_RUNTIME["prefer_localhost_ssh"] = True
        return ssh_result, "localhost-ssh"
    return result, "direct"


def clear_pending_dialogs(state):
    set_pending_dialogs(state, [])


def remove_pending_dialog(state, pending_id):
    remaining = [item for item in get_pending_dialogs(state) if item.get("id") != pending_id]
    set_pending_dialogs(state, remaining)


def find_pending_dialog(state, pending_id="", window_index=0, fallback_first=False):
    dialogs = get_pending_dialogs(state)
    if pending_id:
        pending = next((item for item in dialogs if item.get("id") == pending_id), None)
        if pending:
            return pending
    if window_index:
        pending = next((item for item in dialogs if to_int(item.get("windowIndex"), 0) == to_int(window_index, 0)), None)
        if pending:
            return pending
    if fallback_first and dialogs:
        return dialogs[0]
    return None


def new_pending_dialog_id():
    return f"preview-{int(time.time())}-{random.randint(1000, 9999)}"


def pending_dialog_signature(pending):
    payload = {
        "windowTitle": pending.get("windowTitle", ""),
        "files": [
            {
                "rowIndex": item.get("rowIndex", 0),
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "sizeText": item.get("sizeText", ""),
            }
            for item in (pending.get("files") or [])
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()


def merge_pending_dialogs(previous_dialogs, current_dialogs):
    pools = {}
    for previous in previous_dialogs or []:
        pools.setdefault(previous.get("signature") or pending_dialog_signature(previous), []).append(previous)

    merged = []
    for current in current_dialogs or []:
        signature = pending_dialog_signature(current)
        current["signature"] = signature
        matches = pools.get(signature) or []
        matched = matches.pop(0) if matches else None
        if matched:
            current["id"] = matched.get("id") or new_pending_dialog_id()
            current["createdAt"] = matched.get("createdAt", current.get("createdAt", int(time.time())))
            current["url"] = matched.get("url", current.get("url", ""))
            current["accessMode"] = current.get("accessMode") or matched.get("accessMode", "")
        else:
            current["id"] = new_pending_dialog_id()
            current["createdAt"] = current.get("createdAt", int(time.time()))
            current.setdefault("url", "")
        merged.append(current)
    return merged


def looks_like_preview_summary(value):
    text = (value or "").strip()
    if not text:
        return False
    return text.startswith("已选中") or text.startswith("磁盘剩余") or text.startswith("Disk")


def infer_preview_display_name(pending, fallback_url="", preferred_name=""):
    if preferred_name and preferred_name.strip():
        return preferred_name.strip()
    current_name = (pending.get("name") or "").strip()
    if current_name and not looks_like_preview_summary(current_name):
        return current_name

    files = pending.get("files") or []
    media_files = [item for item in files if (item.get("type") or "").lower() not in ("txt", "html", "htm", "url", "nfo")]
    source = media_files or files
    if len(source) == 1:
        return source[0].get("name") or infer_task_file_name(fallback_url, preferred_name)
    if source:
        first_name = source[0].get("name") or ""
        if "." in first_name:
            return first_name.rsplit(".", 1)[0]
        if first_name:
            return first_name
    return infer_task_file_name(fallback_url, preferred_name)


def close_new_task_windows(config, timeout_seconds=6):
    script = """
tell application "System Events"
  tell process "Thunder"
    set frontmost to true
    set closedCount to 0
    repeat 6 times
      set popupCount to 0
      repeat with i from (count of windows) to 1 by -1
        try
          if (name of window i as text) contains "新建下载任务" then
            set popupCount to popupCount + 1
            try
              repeat with b in buttons of window i
                try
                  if (subrole of b as text) is "AXCloseButton" then
                    click b
                    exit repeat
                  end if
                end try
              end repeat
            on error
              tell window i to perform action "AXRaise"
              key code 53
            end try
            delay 0.15
          end if
        end try
      end repeat
      set closedCount to closedCount + popupCount
      if popupCount is 0 then exit repeat
      delay 0.3
    end repeat
    return "closed\\t" & closedCount
  end tell
end tell
"""
    result, _ = run_accessibility_script(script, timeout_seconds, config)
    if result.returncode != 0 and "can't get window" not in (result.stderr or "").lower():
        detail = (result.stderr or result.stdout or "failed to close preview window").strip()
        log(f"close preview window skipped: {detail}")


def list_preview_window_indexes(config, timeout_seconds=8):
    script = """
on cleanText(rawValue)
  set textValue to rawValue as text
  set AppleScript's text item delimiters to {return, linefeed, tab}
  set textParts to text items of textValue
  set AppleScript's text item delimiters to " "
  set textValue to textParts as text
  set AppleScript's text item delimiters to ""
  return textValue
end cleanText

on isPreviewWindow(windowName)
  if windowName contains "新建下载任务" then return true
  if windowName contains "New Task" then return true
  return false
end isPreviewWindow

tell application "System Events"
  tell process "Thunder"
    set outputLines to {}
    repeat with i from 1 to (count of windows)
      set windowName to ""
      set rowCount to 0
      try
        set windowName to my cleanText(name of window i as text)
      end try
      try
        set rowCount to count of rows of outline 1 of scroll area 1 of window i
      end try
      if rowCount > 0 then
        if my isPreviewWindow(windowName) then
          copy ("WINDOW" & (ASCII character 9) & i & (ASCII character 9) & windowName & (ASCII character 9) & rowCount) to end of outputLines
        end if
      end if
    end repeat
    if (count of outputLines) is 0 then return ""
    set AppleScript's text item delimiters to linefeed
    return outputLines as text
  end tell
end tell
"""
    result, _ = run_accessibility_script(script, timeout_seconds, config)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "failed to list preview windows").strip()
        if "can't get process" in detail.lower() or "new task window not found" in detail.lower():
            return []
        raise RuntimeError(detail)

    indexes = []
    for raw_line in (result.stdout or "").splitlines():
        parts = raw_line.strip().split("\t")
        if len(parts) >= 4 and parts[0] == "WINDOW":
            window_index = to_int(parts[1], 0)
            if window_index > 0:
                indexes.append(window_index)
    return indexes


def extract_preview_dialog(config, window_index, timeout_seconds=12):
    def query(script_text):
        result, mode = run_accessibility_script(script_text, timeout_seconds, config)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "failed to inspect preview window").strip()
            raise RuntimeError(detail)
        return (result.stdout or "").strip(), mode

    header_script = f"""
on cleanText(rawValue)
  set textValue to rawValue as text
  set AppleScript's text item delimiters to {{return, linefeed, tab}}
  set textParts to text items of textValue
  set AppleScript's text item delimiters to " "
  set textValue to textParts as text
  set AppleScript's text item delimiters to ""
  return textValue
end cleanText

tell application "System Events"
  tell process "Thunder"
    set frontmost to true
    if (count of windows) < {window_index} then error "new task window not found"
    tell window {window_index}
      set windowName to ""
      set titleText to ""
      set summaryText to ""
      try
        set windowName to name as text
      end try
      try
        set titleText to value of static text 1
      end try
      try
        set summaryText to value of static text 2
      end try
      return my cleanText(windowName) & (ASCII character 9) & my cleanText(titleText) & (ASCII character 9) & my cleanText(summaryText)
    end tell
  end tell
end tell
"""
    row_count_script = f"""
tell application "System Events"
  tell process "Thunder"
    if (count of windows) < {window_index} then error "new task window not found"
    tell window {window_index}
      return count of rows of outline 1 of scroll area 1
    end tell
  end tell
end tell
"""

    header_output, access_mode = query(header_script)
    window_title, title_text, summary_text = (header_output.split("\t", 2) + ["", ""])[:3]
    row_count_output, row_count_mode = query(row_count_script)
    row_count = to_int(row_count_output, 0)
    if row_count_mode == "localhost-ssh":
        access_mode = row_count_mode

    lines = [
        f"WINDOW\t{window_index}\t{window_title}",
        f"TITLE\t{title_text}",
        f"SUMMARY\t{summary_text}",
        f"COUNT\t{row_count}",
    ]
    for row_index in range(1, row_count + 1):
        row_script = f"""
on cleanText(rawValue)
  set textValue to rawValue as text
  set AppleScript's text item delimiters to {{return, linefeed, tab}}
  set textParts to text items of textValue
  set AppleScript's text item delimiters to " "
  set textValue to textParts as text
  set AppleScript's text item delimiters to ""
  return textValue
end cleanText

tell application "System Events"
  tell process "Thunder"
    if (count of windows) < {window_index} then error "new task window not found"
    tell window {window_index}
      set checkedValue to 0
      set nameText to ""
      set typeText to ""
      set sizeText to ""
      try
        set checkedValue to value of checkbox 1 of UI element 1 of row {row_index} of outline 1 of scroll area 1
      end try
      try
        set nameText to value of static text 1 of UI element 1 of row {row_index} of outline 1 of scroll area 1
      end try
      try
        set typeText to value of static text 1 of UI element 2 of row {row_index} of outline 1 of scroll area 1
      end try
      try
        set sizeText to value of static text 1 of UI element 3 of row {row_index} of outline 1 of scroll area 1
      end try
      return (checkedValue as text) & (ASCII character 9) & my cleanText(nameText) & (ASCII character 9) & my cleanText(typeText) & (ASCII character 9) & my cleanText(sizeText)
    end tell
  end tell
end tell
"""
        row_output, row_mode = query(row_script)
        if row_mode == "localhost-ssh":
            access_mode = row_mode
        lines.append(f"ROW\t{row_index}\t{row_output}")
    return "\n".join(lines), access_mode


def parse_preview_dialog_output(output, url, preferred_name=""):
    pending = {
        "id": "",
        "url": url,
        "name": preferred_name.strip() if preferred_name else "",
        "summary": "",
        "windowIndex": 0,
        "windowTitle": "",
        "fileCount": 0,
        "selectedCount": 0,
        "files": [],
        "createdAt": int(time.time()),
    }
    files = []
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t")
        tag = parts[0]
        if tag == "WINDOW" and len(parts) >= 3:
            pending["windowIndex"] = to_int(parts[1], 0)
            pending["windowTitle"] = parts[2].strip()
        elif tag == "TITLE" and len(parts) > 1 and not pending["name"]:
            pending["name"] = parts[1].strip()
        elif tag == "SUMMARY" and len(parts) > 1:
            pending["summary"] = parts[1].strip()
        elif tag == "ROW" and len(parts) >= 6:
            row_index = to_int(parts[1])
            checked = to_int(parts[2]) == 1
            files.append(
                {
                    "id": str(row_index),
                    "rowIndex": row_index,
                    "name": parts[3].strip(),
                    "type": parts[4].strip(),
                    "sizeText": parts[5].strip(),
                    "checked": checked,
                }
            )
    pending["files"] = files
    pending["fileCount"] = len(files)
    pending["selectedCount"] = sum(1 for item in files if item.get("checked"))
    pending["name"] = infer_preview_display_name(pending, url, preferred_name)
    return pending


def sync_pending_dialogs(state, config):
    previous_dialogs = get_pending_dialogs(state)
    window_indexes = list_preview_window_indexes(config, 8)
    if not window_indexes:
        clear_pending_dialogs(state)
        return []

    current_dialogs = []
    failed_indexes = []
    for window_index in window_indexes:
        try:
            output, access_mode = extract_preview_dialog(config, window_index, 8)
            pending = parse_preview_dialog_output(output, "")
            pending["windowIndex"] = window_index
            pending["accessMode"] = access_mode
            current_dialogs.append(pending)
        except Exception as error:
            log(f"preview scan skipped for window {window_index}: {error}")
            failed_indexes.append(window_index)

    if failed_indexes:
        if not current_dialogs:
            return previous_dialogs
        previous_by_window = {item.get("windowIndex"): item for item in previous_dialogs if item.get("windowIndex")}
        for window_index in failed_indexes:
            fallback = previous_by_window.get(window_index)
            if fallback:
                current_dialogs.append(fallback)
        current_dialogs.sort(key=lambda item: item.get("windowIndex", 0))

    merged = merge_pending_dialogs(previous_dialogs, current_dialogs)
    set_pending_dialogs(state, merged)
    return merged


def begin_task_preview(url, state, config, preferred_name=""):
    previous_dialogs = get_pending_dialogs(state)
    previous_ids = {item.get("id") for item in previous_dialogs}
    wait_for_thunder()

    thunder_open = subprocess.run(
        ["open", "-a", "Thunder", url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if thunder_open.returncode != 0:
        raise RuntimeError((thunder_open.stderr or thunder_open.stdout or "failed to open Thunder").strip())

    deadline = time.time() + max(10, float(config.get("auto_confirm_seconds", 12)) + 12)
    last_error = "preview window not ready"
    while time.time() < deadline:
        try:
            dialogs = sync_pending_dialogs(state, config)
            pending = next((item for item in dialogs if item.get("id") not in previous_ids), None)
            if pending and pending["files"]:
                pending["url"] = url
                if preferred_name and not pending.get("name"):
                    pending["name"] = preferred_name
                set_pending_dialogs(state, dialogs)
                save_state(state)
                log(f"preview ready for {url[:120]} with {pending['fileCount']} files via {pending.get('accessMode', 'direct')}")
                return pending
        except Exception as error:
            last_error = str(error)
        time.sleep(0.25)

    sync_pending_dialogs(state, config)
    save_state(state)
    raise RuntimeError(f"failed to fetch preview file list: {last_error}")


def confirm_task_preview(state, config, pending_id, selected_file_ids, window_index=0):
    pending = find_pending_dialog(state, pending_id, window_index)
    if not pending:
        pending = find_pending_dialog({"pendingDialogs": sync_pending_dialogs(state, config)}, pending_id, window_index)
    if not pending:
        raise RuntimeError("pending preview not found")

    selected_rows = sorted({to_int(item) for item in selected_file_ids if to_int(item) > 0})
    if not selected_rows:
        raise RuntimeError("please select at least one file")

    selected_marker = "|" + "|".join(str(item) for item in selected_rows) + "|"
    selected_row_count = len(selected_rows)
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
delay 0.3
tell application "System Events"
  tell process "Thunder"
    set frontmost to true
    if (count of windows) < {pending['windowIndex']} then error "pending preview window not found"
    tell window {pending['windowIndex']}
      set rowCount to count of rows of outline 1 of scroll area 1
      try
        set masterBox to checkbox 1
        if {selected_row_count} < rowCount and (value of masterBox) is 1 then
          click masterBox
          delay 0.2
        else if {selected_row_count} is rowCount and (value of masterBox) is 0 then
          click masterBox
          delay 0.2
        end if
      end try
      tell outline 1 of scroll area 1
        set selectedRows to "{selected_marker}"
        repeat with i from 1 to rowCount
          set targetSelected to selectedRows contains ("|" & i & "|")
          set rowCheckbox to checkbox 1 of UI element 1 of row i
          set currentValue to value of rowCheckbox
          if targetSelected and currentValue is 0 then click rowCheckbox
          if (not targetSelected) and currentValue is 1 then click rowCheckbox
        end repeat
      end tell
      repeat with b in buttons
        try
          set buttonName to name of b as text
          if my buttonMatches(buttonName) then
            click b
            return "clicked:" & buttonName
          end if
        end try
      end repeat
      key code 36
      return "pressed:return"
    end tell
  end tell
end tell
"""
    result, access_mode = run_accessibility_script(script, 14, config)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "failed to confirm preview").strip()
        raise RuntimeError(detail)

    detail = (result.stdout or "").strip() or "ui-confirm"
    time.sleep(0.4)
    remove_pending_dialog(state, pending.get("id"))
    save_state(state)
    log(f"preview confirmed via {access_mode}: {detail}")
    return {"detail": detail, "accessMode": access_mode, "selectedRows": selected_rows}


def cancel_task_preview(state, config, pending_id="", window_index=0):
    pending = find_pending_dialog(state, pending_id, window_index, fallback_first=not pending_id and not window_index)
    if not pending:
        sync_pending_dialogs(state, config)
        pending = find_pending_dialog(state, pending_id, window_index, fallback_first=not pending_id and not window_index)
    if pending_id and not pending:
        raise RuntimeError("pending preview not found")

    window_index = pending.get("windowIndex", 1) if pending else 1
    script = f"""
tell application "Thunder" to activate
delay 0.2
tell application "System Events"
  tell process "Thunder"
    set frontmost to true
    if (count of windows) < {window_index} then return "no-window"
    set beforeCount to count of windows
    set targetWindow to window {window_index}
    try
      repeat with b in buttons of targetWindow
        try
          if (subrole of b as text) is "AXCloseButton" then
            click b
            exit repeat
          end if
        end try
      end repeat
      delay 0.4
      if (count of windows) < beforeCount then return "clicked:close-button"
      tell targetWindow to perform action "AXRaise"
      key code 53
      delay 0.4
      if (count of windows) < beforeCount then return "pressed:escape"
      keystroke "w" using command down
      delay 0.4
      if (count of windows) < beforeCount then return "pressed:command-w"
      error "preview window still open after close attempts"
    on error
      key code 53
      delay 0.4
      if (count of windows) < beforeCount then return "pressed:escape"
      keystroke "w" using command down
      delay 0.4
      if (count of windows) < beforeCount then return "pressed:command-w"
      error "preview window still open after fallback close attempts"
    end try
  end tell
end tell
"""
    result, access_mode = run_accessibility_script(script, 8, config)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "failed to cancel preview").strip()
        raise RuntimeError(detail)

    detail = (result.stdout or "").strip() or "cancelled"
    time.sleep(0.3)
    if pending:
        remove_pending_dialog(state, pending.get("id"))
    save_state(state)
    log(f"preview cancelled via {access_mode}: {detail}")
    return {"detail": detail, "accessMode": access_mode}


def confirm_thunder_download(timeout_seconds=12, config=None):
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
    result, mode = run_accessibility_script(script, timeout_seconds, config)
    if result.returncode == 0:
        suffix = " via localhost ssh" if mode == "localhost-ssh" else ""
        log(f"thunder confirm result{suffix}: {(result.stdout or '').strip()}")
        return

    detail = (result.stderr or result.stdout or "unknown accessibility error").strip()
    raise RuntimeError(f"已打开迅雷新建任务窗口，但自动点击立即下载失败：{detail}")


def send_agent_event(client, config, event):
    client.send_json({"role": "agent", "token": config["token"], "event": event})


def send_agent_snapshot(client, config, state):
    tasks = read_tasks(config)
    snapshot = build_snapshot(tasks, state, config)
    client.send_json({"role": "agent", "token": config["token"], "payload": snapshot})


def task_mode_label(mode):
    if mode == "direct":
        return "direct"
    if mode == "open-url":
        return "open-url"
    return mode or "unknown"


def status_refresh_interval(tasks, state, config):
    if get_pending_dialogs(state):
        return max(0.15, to_float(config.get("interactive_status_poll_interval_seconds"), 0.35))
    if migration_task_id() or any(task.get("status") == "downloading" for task in tasks):
        return max(0.25, to_float(config.get("busy_status_poll_interval_seconds"), 0.75))
    return max(
        0.5,
        to_float(
            config.get("idle_status_poll_interval_seconds"),
            to_float(config.get("poll_interval_seconds"), 2),
        ),
    )


def command_poll_interval(config):
    return max(0.05, to_float(config.get("command_poll_interval_seconds"), 0.12))


def handle_commands(client, config, state):
    handled = 0
    for message in client.receive_json():
        if message.get("type") != "command":
            continue
        command = message.get("command")
        command_id = message.get("id")
        try:
            if command == "addTask":
                url = (message.get("url") or "").strip()
                preferred_name = (message.get("name") or "").strip()
                result = add_task_to_thunder(url, config, preferred_name)
                mode = task_mode_label(result.get("mode"))
                log(f"add task command accepted via {mode}: {url[:120]}")
                send_agent_event(
                    client,
                    config,
                    {
                        "kind": "command",
                        "status": "accepted",
                        "title": f"Mac 迅雷已接收任务（{mode}）",
                        "message": f"{url[:140]} [{result.get('detail', 'scheduled')}]",
                        "commandId": command_id,
                    },
                )
            elif command == "previewTask":
                url = (message.get("url") or "").strip()
                preferred_name = (message.get("name") or "").strip()
                pending = begin_task_preview(url, state, config, preferred_name)
                send_agent_event(
                    client,
                    config,
                    {
                        "kind": "preview",
                        "status": "ready",
                        "title": "待下载文件已就绪",
                        "message": f"{pending['name']} / {pending['selectedCount']}/{pending['fileCount']}",
                        "commandId": command_id,
                    },
                )
            elif command == "confirmPreviewTask":
                pending_id = (message.get("pendingId") or "").strip()
                selected_file_ids = message.get("selectedFileIds") or []
                window_index = to_int(message.get("windowIndex"), 0)
                result = confirm_task_preview(state, config, pending_id, selected_file_ids, window_index)
                send_agent_event(
                    client,
                    config,
                    {
                        "kind": "preview",
                        "status": "accepted",
                        "title": "已按所选文件开始下载",
                        "message": f"{len(result['selectedRows'])} 个文件 [{result['detail']}]",
                        "commandId": command_id,
                    },
                )
            elif command == "cancelPreviewTask":
                pending_id = (message.get("pendingId") or "").strip()
                window_index = to_int(message.get("windowIndex"), 0)
                result = cancel_task_preview(state, config, pending_id, window_index)
                send_agent_event(
                    client,
                    config,
                    {
                        "kind": "preview",
                        "status": "cancelled",
                        "title": "待下载任务已取消",
                        "message": result["detail"],
                        "commandId": command_id,
                    },
                )
            elif command == "migrateFile":
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
            elif command == "deleteFile":
                file_path = (message.get("filePath") or "").strip()
                display_name = (message.get("name") or "").strip()
                result = delete_selected_file(file_path, config, display_name)
                send_agent_event(
                    client,
                    config,
                    {
                        "kind": "delete",
                        "status": "done",
                        "title": "本地文件已删除",
                        "message": f"{result['name']}，释放 {result['bytes']} 字节",
                        "commandId": command_id,
                    },
                )
            elif command == "controlTask":
                task_id = (message.get("taskId") or "").strip()
                action = (message.get("action") or "").strip().lower()
                delete_files = bool(message.get("deleteFiles"))
                result = control_task(task_id, action, config, delete_files)
                task_name = result.get("task", {}).get("name") or task_id
                action_titles = {
                    "start": "任务已开始",
                    "pause": "任务已暂停",
                    "delete": "任务已删除",
                }
                detail = result.get("detail", action)
                if action == "delete":
                    detail = f"{detail} / deleteFiles={str(result.get('deleteFiles', False)).lower()}"
                send_agent_event(
                    client,
                    config,
                    {
                        "kind": "task",
                        "status": "done",
                        "title": action_titles.get(action, "任务已处理"),
                        "message": f"{task_name} [{detail}]",
                        "commandId": command_id,
                    },
                )
            else:
                continue
            save_state(state)
            send_agent_snapshot(client, config, state)
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
            save_state(state)
            send_agent_snapshot(client, config, state)
        handled += 1
    return handled


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


def build_snapshot(tasks, state, config):
    total_speed = sum(task.get("speed", 0) for task in tasks)
    active = sum(1 for task in tasks if task.get("status") == "downloading")
    completed = sum(1 for task in tasks if task.get("status") == "completed")
    pending_dialogs = get_pending_dialogs(state)
    return {
        "mac": {
            "host": socket.gethostname(),
            "user": os.environ.get("USER", ""),
            "thunderRunning": thunder_running(),
            "taskCreateMode": task_mode_label(config.get("task_create_mode")),
            "taskCreateFallbackToUi": bool(config.get("direct_create_fallback_to_ui", True)),
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
        "pendingDialogs": pending_dialogs,
        "pendingDialog": pending_dialogs[0] if pending_dialogs else None,
    }


def main():
    config = load_config()
    state = load_state()
    clear_pending_dialogs(state)
    save_state(state)
    client = WebSocketClient(config["server_ws"])
    log("thunder agent started")
    next_refresh_at = 0.0
    while True:
        try:
            now = time.monotonic()
            if now >= next_refresh_at:
                tasks = read_tasks(config)
                sync_pending_dialogs(state, config)
                drain_background_migration(state)
                save_state(state)
                snapshot = build_snapshot(tasks, state, config)
                client.send_json({"role": "agent", "token": config["token"], "payload": snapshot})
                if config.get("auto_migration_enabled", False) and not get_pending_dialogs(state):
                    handle_migrations(tasks, state, config)
                    save_state(state)
                next_refresh_at = time.monotonic() + status_refresh_interval(tasks, state, config)
            handled = handle_commands(client, config, state)
            if handled:
                next_refresh_at = 0.0
                continue
            time.sleep(command_poll_interval(config))
        except Exception as error:
            log(f"loop error: {error}")
            log(traceback.format_exc().strip())
            client.close()
            next_refresh_at = 0.0
            time.sleep(3)


if __name__ == "__main__":
    main()

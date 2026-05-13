import datetime
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXTENSION_ROOT = ROOT

FILES = {
    "queue": os.path.join(ROOT, "queue.txt"),
    "in_progress": os.path.join(ROOT, "in_progress.txt"),
    "completed": os.path.join(ROOT, "completed.txt"),
    "failed": os.path.join(ROOT, "failed.txt"),
    "skipped": os.path.join(ROOT, "skipped.txt"),
    "error_log": os.path.join(ROOT, "error.log"),
}

GALLERY_DL_PATH = os.environ.get("GALLERY_DL_PATH", "gallery-dl")

state_lock = threading.Lock()
cookie_lock = threading.Lock()

download_queue = []
active_slots = [None, None]
active_procs = [None, None]
concurrency = 2
download_path = ""


def log_error(message: str) -> None:
    ts = datetime.datetime.now().strftime("[%Y-%m-%dT%H:%M:%S]")
    with open(FILES["error_log"], "a", encoding="utf-8") as handle:
        handle.write(f"{ts} {message}\n")


def read_file(name: str) -> list[str]:
    path = FILES[name]
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def write_file(name: str, lines: list[str]) -> None:
    path = FILES[name]
    with open(path, "w", encoding="utf-8") as handle:
        if lines:
            handle.write("\n".join(lines) + "\n")


def append_file(name: str, line: str) -> None:
    path = FILES[name]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def send_message(message: dict) -> None:
    data = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def send_state() -> None:
    with state_lock:
        state = {
            "queue": list(download_queue),
            "in_progress": [slot for slot in active_slots if slot],
            "concurrency": concurrency,
            "download_path": download_path,
        }
    send_message({"type": "state", "state": state})


def read_message() -> dict | None:
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    message_length = struct.unpack("<I", raw_length)[0]
    data = sys.stdin.buffer.read(message_length)
    if not data:
        return None
    return json.loads(data.decode("utf-8"))


def copy_cookies() -> str | None:
    with cookie_lock:
        src = os.path.expandvars(
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies"
        )
        if not os.path.exists(src):
            log_error(f"WARNING: Chrome cookie DB not found at {src}")
            return None
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite")
            tmp_path = tmp.name
            tmp.close()
            shutil.copy2(src, tmp_path)
            return tmp_path
        except Exception as exc:  # noqa: BLE001
            log_error(f"Cookie copy failed: {exc}")
            return None


def download_worker(slot: int) -> None:
    while True:
        with state_lock:
            if not download_queue or slot >= concurrency:
                active_slots[slot] = None
                active_procs[slot] = None
                write_file("in_progress", [s for s in active_slots if s])
                should_wait = True
                url = None
            else:
                url = download_queue.pop(0)
                active_slots[slot] = url
                active_procs[slot] = None
                write_file("queue", download_queue)
                write_file("in_progress", [s for s in active_slots if s])
                should_wait = False
        if should_wait:
            time.sleep(0.5)
            continue
        send_state()

        cmd = [GALLERY_DL_PATH, "--dest", download_path]
        cookie_tmp = copy_cookies()
        if cookie_tmp:
            cmd += ["--cookies", cookie_tmp]
        cmd.append(url)

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with state_lock:
            active_procs[slot] = proc

        proc.wait()

        if cookie_tmp:
            try:
                os.unlink(cookie_tmp)
            except Exception:  # noqa: BLE001
                pass

        with state_lock:
            active_slots[slot] = None
            active_procs[slot] = None
            ts = datetime.datetime.now().strftime("[%Y-%m-%dT%H:%M:%S]")
            if proc.returncode == 0:
                append_file("completed", f"{ts} {url}")
            else:
                append_file("failed", f"{ts} {url} | exit_code={proc.returncode}")
            write_file("in_progress", [s for s in active_slots if s])
        send_state()


def startup() -> None:
    global download_queue
    download_queue = read_file("queue")
    in_progress = read_file("in_progress")
    if in_progress:
        download_queue = in_progress + download_queue
        write_file("queue", download_queue)
        write_file("in_progress", [])

    for i in range(2):
        t = threading.Thread(target=download_worker, args=(i,), daemon=True)
        t.start()


def dispatch(msg: dict) -> None:
    action = msg.get("action")
    if action == "add_url":
        url = msg.get("url", "").strip()
        if not url or not url.startswith(("http://", "https://")):
            return
        with state_lock:
            download_queue.append(url)
            write_file("queue", download_queue)
        send_state()
    elif action == "skip_current":
        slot = msg.get("slot", 0)
        with state_lock:
            url = active_slots[slot]
            proc = active_procs[slot]
        if proc:
            proc.terminate()
        if url:
            ts = datetime.datetime.now().strftime("[%Y-%m-%dT%H:%M:%S]")
            with state_lock:
                append_file("skipped", f"{ts} {url}")
        send_state()
    elif action == "cancel_all":
        with state_lock:
            ts = datetime.datetime.now().strftime("[%Y-%m-%dT%H:%M:%S]")
            for url in download_queue:
                append_file("skipped", f"{ts} {url}")
            download_queue.clear()
            write_file("queue", [])
            for i in range(2):
                if active_procs[i]:
                    active_procs[i].terminate()
                if active_slots[i]:
                    append_file("skipped", f"{ts} {active_slots[i]}")
            write_file("in_progress", [])
        send_state()
    elif action == "set_concurrency":
        global concurrency
        val = msg.get("value", 2)
        if val in (1, 2):
            with state_lock:
                concurrency = val
        send_state()
    elif action == "set_download_path":
        global download_path
        p = msg.get("path", "").strip()
        if p:
            download_path = p
        send_state()
    elif action == "open_folder":
        folder_type = msg.get("type", "extension")
        if folder_type == "extension":
            path = EXTENSION_ROOT
        else:
            path = download_path
        if path and os.path.exists(path):
            subprocess.Popen(["explorer", path])
    elif action == "get_state":
        send_state()


def main() -> None:
    startup()
    while True:
        try:
            msg = read_message()
        except Exception as exc:  # noqa: BLE001
            log_error(f"Message read failed: {exc}")
            break
        if msg is None:
            break
        try:
            dispatch(msg)
        except Exception as exc:  # noqa: BLE001
            log_error(f"Dispatch failed: {exc}")


if __name__ == "__main__":
    main()

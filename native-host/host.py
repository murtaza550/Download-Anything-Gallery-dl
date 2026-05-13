import subprocess
import threading
import json
import struct
import sys
import os
import queue
import datetime
import shutil
import tempfile
import time

EXTENSION_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = {
    "queue": os.path.join(EXTENSION_ROOT, "queue.txt"),
    "in_progress": os.path.join(EXTENSION_ROOT, "in_progress.txt"),
    "completed": os.path.join(EXTENSION_ROOT, "completed.txt"),
    "failed": os.path.join(EXTENSION_ROOT, "failed.txt"),
    "skipped": os.path.join(EXTENSION_ROOT, "skipped.txt"),
    "error_log": os.path.join(EXTENSION_ROOT, "error.log"),
}

state_lock = threading.Lock()
cookie_lock = threading.Lock()

GALLERY_DL_PATH = None
GALLERY_DL_AVAILABLE = False
download_path = ""
concurrency = 2
download_queue = []
active_slots = [None, None]
active_procs = [None, None]

write_lock = threading.Lock()
error_lock = threading.Lock()


def send_message(msg):
    payload = json.dumps(msg).encode("utf-8")
    header = struct.pack("<I", len(payload))
    with write_lock:
        sys.stdout.buffer.write(header)
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()


def _read_exact(num_bytes):
    data = b""
    while len(data) < num_bytes:
        chunk = sys.stdin.buffer.read(num_bytes - len(data))
        if chunk == b"":
            return None
        data += chunk
    return data


def read_message():
    header = _read_exact(4)
    if header is None:
        return None
    length = struct.unpack("<I", header)[0]
    if length == 0:
        return {}
    body = _read_exact(length)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def touch_files():
    for path in FILES.values():
        if not os.path.exists(path):
            open(path, "a").close()


def write_file(key, lines):
    data = "\n".join(lines)
    with open(FILES[key], "w", encoding="utf-8") as handle:
        handle.write(data)


def append_file(key, entry):
    with open(FILES[key], "a", encoding="utf-8") as handle:
        handle.write(entry + "\n")


def read_file_lines(key):
    path = FILES[key]
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle.read().splitlines() if line.strip()]


def log_error(msg):
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entry = f"{timestamp} {msg}"
    with error_lock:
        with open(FILES["error_log"], "a", encoding="utf-8") as handle:
            handle.write(entry + "\n")


def find_gallery_dl():
    path = shutil.which("gallery-dl")
    if path:
        return path
    path = shutil.which("gallery-dl.exe")
    if path:
        return path
    fallback_paths = []
    for minor in range(9, 14):
        fallback_paths.append(
            os.path.expandvars(
                rf"%APPDATA%\Python\Python3{minor}\Scripts\gallery-dl.exe"
            )
        )
        fallback_paths.append(
            os.path.expandvars(
                rf"%LOCALAPPDATA%\Programs\Python\Python3{minor}\Scripts\gallery-dl.exe"
            )
        )
    for candidate in fallback_paths:
        if os.path.isfile(candidate):
            return candidate
    return None


def startup():
    global GALLERY_DL_PATH, GALLERY_DL_AVAILABLE
    touch_files()
    GALLERY_DL_PATH = find_gallery_dl()
    if GALLERY_DL_PATH is None:
        log_error("gallery-dl not found")
        send_message(
            {
                "type": "error",
                "message": "gallery-dl not found. Ensure it is installed and on your PATH.",
            }
        )
        GALLERY_DL_AVAILABLE = False
    else:
        GALLERY_DL_AVAILABLE = True

    with state_lock:
        in_progress = read_file_lines("in_progress")
        if in_progress:
            download_queue[:0] = in_progress
        write_file("in_progress", [])
        queued = read_file_lines("queue")
        if queued:
            download_queue.extend(queued)
        write_file("queue", download_queue)


def send_state():
    with state_lock:
        state_message = {
            "type": "state_update",
            "queue": list(download_queue),
            "active": [
                {
                    "slot": i,
                    "url": active_slots[i],
                    "status": "downloading" if active_slots[i] else "idle",
                }
                for i in range(2)
            ],
            "concurrency": concurrency,
            "download_path": download_path,
        }
    send_message(state_message)


def dispatch(msg):
    action = msg.get("action")
    if action == "ping":
        send_message({"type": "pong"})
    elif action == "get_state":
        send_state()
    else:
        pass


def main():
    try:
        startup()
        while True:
            msg = read_message()
            if msg is None:
                break
            try:
                dispatch(msg)
            except Exception as exc:
                log_error(str(exc))
    except Exception as exc:
        log_error(str(exc))


if __name__ == "__main__":
    main()

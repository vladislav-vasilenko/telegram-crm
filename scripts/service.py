#!/usr/bin/env python3
"""Start/open/stop the one local service used by the Dock application."""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DATA = PROJECT / ".data"
URL = "http://127.0.0.1:4180"


def healthy():
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=1) as response:
            return json.load(response).get("application") == "telegram-dialogue-crm"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def start():
    os.umask(0o077)
    DATA.mkdir(mode=0o700, exist_ok=True)
    DATA.chmod(0o700)
    with (DATA / "launcher.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if healthy():
            return
        python = PROJECT / ".venv" / "bin" / "python"
        if not python.exists():
            raise RuntimeError("Не найдено окружение .venv. Следуйте SETUP.md в папке Telegram CRM.")
        with (DATA / "server.log").open("ab") as log:
            process = subprocess.Popen([str(python), "-m", "telegram_crm", "serve", "--data-dir", str(DATA), "--port", "4180"],
                                       cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        (DATA / "server.pid").write_text(str(process.pid))
        for _ in range(100):
            if healthy():
                return
            if process.poll() is not None:
                raise RuntimeError("Сервис не запустился. Подробности: " + str(DATA / "server.log"))
            time.sleep(0.25)
        raise RuntimeError("Сервис не ответил за 25 секунд. Проверьте .data/server.log и порт 4180.")


def stop():
    pid_file = DATA / "server.pid"
    if not pid_file.exists():
        print("Сервис, запущенный ярлыком, не найден.")
        return
    pid = int(pid_file.read_text())
    command = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    if "telegram_crm" not in command or str(PROJECT) not in command:
        raise RuntimeError("PID не принадлежит этому сервису. Остановка отменена.")
    os.kill(pid, signal.SIGTERM)
    print("Сервис остановлен.")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "open"
    try:
        if command == "stop":
            stop()
        elif command == "status":
            print("running" if healthy() else "stopped")
        else:
            start()
            if command == "url":
                print(URL)
            else:
                subprocess.run(["/usr/bin/open", URL], check=True)
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)


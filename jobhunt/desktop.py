"""JobQuestAI.exe entry point.

Double-click: starts the server quietly and opens the UI in its own app window (Edge or Chrome in --app mode, so no
tabs or address bar). Closing that window stops the server. Launching again while it runs just opens another window.
With arguments it behaves like the command line instead: `JobQuestAI.exe refresh --open-if-new`, `status`, ...
"""
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import settings


def _browser():
    env = os.path.expandvars
    for c in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
              r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
              r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe", r"%LocalAppData%\Google\Chrome\Application\chrome.exe"):
        if Path(env(c)).exists():
            return env(c)
    return None


def open_window(url):
    """Open the app window. Returns the browser process (alive while the window is open) or None."""
    exe = _browser()
    if not exe:
        webbrowser.open(url)
        return None
    # Its own profile folder keeps the window independent of your normal browser, so its process ends when you
    # close it - which is how the launcher knows to stop the server.
    profile = settings.HOME / "app-window"
    return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                             "--no-default-browser-check", "--window-size=1440,920", "--disable-sync"])


REMINDER_HOURS = (13, 18)       # local time; the scheduled task covers 09:00 and 21:00


def _background(cfg):
    """While the app is open: keep jobs fresh (every `auto_refresh_hours`) and nudge toward the daily target."""
    from datetime import date, datetime
    from . import api, db, notify
    hours = cfg["server"].get("auto_refresh_hours", 12)
    while True:
        time.sleep(600)
        try:
            api.maybe_auto_refresh(hours)
            _maybe_mail_sync(hours)
            now = datetime.now()
            key = f"{date.today().isoformat()}-{now.hour}"
            if now.hour in REMINDER_HOURS and db.get_setting("last_reminder") != key:
                db.set_setting("last_reminder", key)
                notify.target_reminder()
        except Exception as e:                     # a reminder must never take the app down
            print(f"background task: {e}")


def _maybe_mail_sync(hours):
    """Check Gmail for application updates as often as jobs refresh. Only proposes changes."""
    from datetime import datetime, timedelta, timezone
    from . import db, mail
    if not mail.connected():
        return
    last = db.get_setting("mail_last_sync")
    if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=hours):
        return
    try:
        mail.sync()
    except mail.MailError as e:
        print(f"mail sync: {e}")


def _profile_in_use():
    """Chromium holds <user-data-dir>/lockfile open without sharing while any window of that profile is open."""
    lock = settings.HOME / "app-window" / "lockfile"
    if not lock.exists():
        return False
    try:
        with open(lock, "a"):
            return False
    except OSError:
        return True


def _message(text, title="JobQuest.AI"):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        print(text)


def _quiet_streams():
    """A windowed .exe has no console; send any output to a log file instead of losing it (or crashing on it)."""
    if sys.stdout is None or sys.stderr is None:
        settings.HOME.mkdir(parents=True, exist_ok=True)
        log = open(settings.HOME / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def _daily_backup():
    try:
        from . import backup
        backup.auto_daily()
    except Exception as e:                       # a failed backup must never stop the app opening
        print(f"daily backup skipped: {e}")


def main():
    _quiet_streams()
    args = sys.argv[1:]
    from . import cli
    if args:
        return cli.main(args)

    cfg = settings.load_config()
    port = cfg["server"]["port"]
    url = f"http://127.0.0.1:{port}/"
    if cli._running(port):
        open_window(url)
        return 0

    import uvicorn
    from . import api
    api.app.state.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning",
                                           log_config=None, use_colors=False))
    thread = threading.Thread(target=server.run, daemon=True, name="server")
    thread.start()
    for _ in range(150):
        if server.started or not thread.is_alive():
            break
        time.sleep(0.1)
    if not server.started:
        _message(f"JobQuest.AI couldn't start: port {port} is in use by another program.\n\n"
                 f"Close it, or change [server] port in config.toml.")
        return 1
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} JobQuest.AI started on {url}")
    _daily_backup()
    api.maybe_auto_refresh(cfg["server"].get("auto_refresh_hours", 12))

    threading.Thread(target=_background, args=(cfg,), daemon=True, name="reminders").start()
    window = open_window(url)
    if window is None:              # no app-mode browser: keep serving until Windows ends the process
        thread.join()
        return 0
    window.wait()
    # If a window from an earlier run was still open, the new browser process hands over to it and exits at once.
    # Keep serving while that window's profile is still locked.
    while _profile_in_use():
        time.sleep(3)
    server.should_exit = True
    thread.join(timeout=8)
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} window closed, JobQuest.AI stopped")
    return 0

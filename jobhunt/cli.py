"""Command line: python -m jobhunt [serve | refresh | add-resume FILE | status], or JobQuestAI.exe with the same arguments."""
import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from . import settings


def _running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return json.loads(r.read()).get("app") == "JobQuest.AI"
    except Exception:
        return False


def serve(port, open_browser=True):
    url = f"http://127.0.0.1:{port}/"
    if _running(port):
        print(f"JobQuest.AI is already running: {url}")
        if open_browser:
            webbrowser.open(url)
        return
    import uvicorn
    from . import api
    cfg = settings.load_config()
    api.app.state.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    from .desktop import _daily_backup
    _daily_backup()
    api.maybe_auto_refresh(cfg["server"].get("auto_refresh_hours", 12))
    if open_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    print(f"JobQuest.AI running at {url}  (close this window or press Ctrl+C to stop)")
    uvicorn.run(api.app, host="127.0.0.1", port=port, log_level="warning")


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    cfg = settings.load_config()
    ap = argparse.ArgumentParser(prog="jobhunt")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("serve", help="start the web app (default)")
    p.add_argument("--port", type=int, default=cfg["server"]["port"])
    p.add_argument("--no-open", action="store_true")
    p = sub.add_parser("refresh", help="search for new jobs now (used by the daily task)")
    p.add_argument("--open-if-new", action="store_true", help="open the app if new jobs turned up")
    p = sub.add_parser("add-resume", help="add a resume from the command line")
    p.add_argument("file")
    p.add_argument("--label", default="")
    sub.add_parser("status", help="show the last refresh")
    sub.add_parser("demo", help="fill an empty JOBHUNT_HOME with fictional data (for screenshots)")
    a = ap.parse_args(argv)

    if a.cmd in (None, "serve"):
        serve(getattr(a, "port", cfg["server"]["port"]), not getattr(a, "no_open", False))
    elif a.cmd == "refresh":
        from . import refresh
        orig, phases = refresh.PROGRESS.update, set()

        def update(**kw):                      # print each phase once, for the scheduled task's log
            orig(**kw)
            if kw.get("phase") and kw["phase"] not in phases:
                phases.add(kw["phase"])
                print(kw["phase"] + " ...", flush=True)
        refresh.PROGRESS.update = update
        r = refresh.run()
        print(json.dumps(r, indent=1))
        try:
            from . import notify
            notify.target_reminder()               # the 09:00 / 21:00 task doubles as the daily-target nudge
            from . import mail
            if mail.connected():
                n = len(mail.sync())
                if n:
                    notify.toast("JobQuest.AI", f"{n} application update(s) found in Gmail - review them in the Tracker.")
        except Exception as e:
            print(f"reminder skipped: {e}")
        if a.open_if_new and r["new"]:
            port = cfg["server"]["port"]
            if _running(port):
                pass          # the open app notices the new search by itself and reloads its list
            elif getattr(sys, "frozen", False):          # running as JobQuestAI.exe: start the app window
                subprocess.Popen([sys.executable])
            elif (settings.ROOT / "JobQuestAI.exe").exists():   # the built app is next to the code
                subprocess.Popen([str(settings.ROOT / "JobQuestAI.exe")])
            else:          # start the app in its own console window; it opens the browser itself
                subprocess.Popen(["cmd", "/c", "start", "JobQuest.AI", str(settings.ROOT / "start.cmd")],
                                 cwd=settings.ROOT)
    elif a.cmd == "demo":
        from . import demo
        print(f"Demo data written to {demo.seed()}")
    elif a.cmd == "add-resume":
        from pathlib import Path
        from . import ai, profile
        f = Path(a.file)
        rid, err = profile.add_resume(f.read_bytes(), f.name, a.label, ai.client_from_settings(cfg))
        print(f"Added resume #{rid}: {f.name}" + (f"  (AI couldn't read it: {err})" if err else ""))
    elif a.cmd == "status":
        from . import db
        print(json.dumps(db.last_run(ok_only=False), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

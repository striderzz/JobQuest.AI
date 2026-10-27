"""Windows desktop notifications (toast) without extra packages: a short PowerShell WinRT call."""
import base64
import subprocess
import sys

# PowerShell's own registered app id; toasts need one, and this works on every Windows 10/11 install
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def _xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


def toast(title, message):
    """Show a toast; returns False when that isn't possible (not Windows, PowerShell blocked)."""
    if sys.platform != "win32":
        return False
    xml = (f"<toast><visual><binding template='ToastGeneric'><text>{_xml_escape(title)}</text>"
           f"<text>{_xml_escape(message)}</text></binding></visual></toast>").replace("'", "''")
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml('{xml}');"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_APP_ID}')"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                           capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return False


def target_reminder(force=False):
    """Nudge toward the daily application target. Returns the message shown, or None."""
    from . import stats
    done, tgt = stats.today_count(), stats.target()
    if done >= tgt and not force:
        return None
    left = max(tgt - done, 0)
    msg = (f"{done} of {tgt} applications today. {left} more to hit your target - open JobQuest.AI for fresh matches."
           if left else f"Target met: {done} applications today. Nice work.")
    return msg if toast("JobQuest.AI daily target", msg) else None

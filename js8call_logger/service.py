"""A systemd user unit, so logging resumes after a reboot."""
import os
import shutil
import subprocess

NAME = "js8call-logger.service"


def unit_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "systemd", "user")


def unit_path() -> str:
    return os.path.join(unit_dir(), NAME)


def _quote(path: str) -> str:
    """systemd splits ExecStart on spaces; quote when it must."""
    if " " not in path and "\t" not in path:
        return path
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def unit_text(exec_path: str, home: str | None = None,
              doc: str | None = None) -> str:
    env = f"Environment=\"JS8CALL_LOGGER_HOME={home}\"\n" if home and " " in home else (
        f"Environment=JS8CALL_LOGGER_HOME={home}\n" if home else "")
    # systemd wants a URI here and rejects one containing spaces
    docs = f"Documentation=file:{doc}\n" if doc and " " not in doc else ""
    exec_path = _quote(exec_path)
    return f"""[Unit]
Description=JS8Call logger
{docs}After=pipewire.service wireplumber.service network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_path} start --foreground
{env}Restart=on-failure
RestartSec=10
TimeoutStopSec=45

[Install]
WantedBy=default.target
"""


def available() -> bool:
    """True when a user systemd is reachable."""
    if not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(["systemctl", "--user", "is-system-running"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    # "running", "degraded", "starting" all mean we can talk to it
    return r.stdout.strip() in ("running", "degraded", "starting", "initializing")


def _run(*args: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(["systemctl", "--user", *args],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    out = (r.stdout or r.stderr or "").strip()
    return r.returncode == 0, out


def install(exec_path: str, home: str | None = None, doc: str | None = None,
            start: bool = True) -> tuple[bool, str]:
    os.makedirs(unit_dir(), exist_ok=True)
    with open(unit_path(), "w", encoding="utf-8") as fh:
        fh.write(unit_text(exec_path, home, doc))
    ok, out = _run("daemon-reload")
    if not ok:
        return False, out
    ok, out = _run("enable", NAME)
    if not ok:
        return False, out
    if start:
        ok, out = _run("restart", NAME)
        return ok, out or "enabled and started"
    return True, "enabled"


def remove(started: bool = True) -> tuple[bool, str]:
    _run("disable", "--now", NAME)
    try:
        os.remove(unit_path())
    except FileNotFoundError:
        pass
    ok, out = _run("daemon-reload")
    return ok, "removed" if ok else out


def linger_enabled() -> bool:
    """A user unit only runs while the user has a session unless lingered."""
    user = os.environ.get("USER") or ""
    try:
        r = subprocess.run(["loginctl", "show-user", user, "-p", "Linger"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return "Linger=yes" in (r.stdout or "")


def status() -> tuple[bool, str]:
    return _run("status", "--no-pager", "-n", "5", NAME)

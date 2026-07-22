"""Upload short-telegram-bot-lite to VPS using deploy/ssh_credentials.env from Sano (трекер калорий)."""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath

import paramiko
from dotenv import load_dotenv

TRACKER_ROOT = Path(r"c:\Users\Администратор\OneDrive\Рабочий стол\трекер калорий")
CREDENTIALS_FILE = TRACKER_ROOT / "deploy" / "ssh_credentials.env"
LOCAL_ROOT = Path(__file__).resolve().parent.parent
REMOTE_ROOT = PurePosixPath("/opt/short-telegram-bot-lite")

SKIP_DIR_NAMES = frozenset(
    {".venv", "__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
SKIP_FILES = frozenset({".env"})
SKIP_SUFFIXES = frozenset({".pyc"})


def _mkdir_p(sftp: paramiko.SFTPClient, remote_dir: PurePosixPath) -> None:
    current = PurePosixPath("/")
    for part in remote_dir.parts[1:]:
        current /= part
        try:
            sftp.stat(str(current))
        except FileNotFoundError:
            sftp.mkdir(str(current))


def _upload_tree(sftp: paramiko.SFTPClient, local: Path, remote: PurePosixPath) -> None:
    for child in sorted(local.iterdir(), key=lambda p: p.name.lower()):
        name = child.name
        if name in SKIP_DIR_NAMES:
            continue
        if name.startswith("pytest-cache"):
            continue
        rpath = remote / name
        if child.is_dir():
            _mkdir_p(sftp, rpath)
            _upload_tree(sftp, child, rpath)
        else:
            if name in SKIP_FILES or child.suffix in SKIP_SUFFIXES:
                continue
            sftp.put(str(child), str(rpath))


def _run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 300) -> tuple[int, str, str]:
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def main() -> int:
    if not CREDENTIALS_FILE.is_file():
        print(f"Missing credentials file: {CREDENTIALS_FILE}", file=sys.stderr)
        return 1

    load_dotenv(CREDENTIALS_FILE)
    host = os.environ.get("SANO_VPS_HOST", "").strip()
    user = os.environ.get("SANO_VPS_USER", "root").strip()
    password = os.environ.get("SSH_PASS", "").strip()
    if not host or not password:
        print("Set SANO_VPS_HOST and SSH_PASS in трекер калорий/deploy/ssh_credentials.env", file=sys.stderr)
        return 1

    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=45)

    code, out, err = _run(
        ssh,
        f"mkdir -p {REMOTE_ROOT} && chmod 755 {REMOTE_ROOT}",
        timeout=60,
    )
    if code != 0:
        print(err or out, file=sys.stderr)
        ssh.close()
        return code

    sftp = ssh.open_sftp()
    _mkdir_p(sftp, REMOTE_ROOT)
    _upload_tree(sftp, LOCAL_ROOT, REMOTE_ROOT)
    sftp.close()
    print(f"Uploaded {LOCAL_ROOT} -> {REMOTE_ROOT}")

    remote_setup = (
        f"set -e; cd {REMOTE_ROOT}; "
        "python3 -m venv .venv 2>/dev/null || true; "
        ". .venv/bin/activate; pip install -q -r requirements.txt"
    )
    code, out, err = _run(ssh, remote_setup, timeout=600)
    print("===== pip install =====")
    if out.strip():
        print(out)
    if err.strip():
        print(err, file=sys.stderr)

    restart_cmds = [
        "systemctl restart short-telegram-bot-lite",
        "systemctl try-restart short-telegram-bot 2>/dev/null || true",
        "systemctl try-restart short-bot 2>/dev/null || true",
    ]
    for cmd in restart_cmds:
        _run(ssh, cmd, timeout=30)

    code2, out2, err2 = _run(
        ssh,
        "systemctl list-units --type=service --all --no-pager 2>/dev/null | grep -iE 'short|signal|telegram' || true",
        timeout=30,
    )
    if out2.strip():
        print("===== matching systemd units =====")
        print(out2)

    ssh.close()
    if code != 0:
        return code
    print("Deploy finished. If the bot is not under systemd, restart your process manually on the server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

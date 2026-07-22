"""systemctl restart short-telegram-bot-lite via ssh_credentials.env (Sano tracker)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko
from dotenv import load_dotenv

TRACKER = Path(r"c:\Users\Администратор\OneDrive\Рабочий стол\трекер калорий")
load_dotenv(TRACKER / "deploy" / "ssh_credentials.env")

host = os.environ.get("SANO_VPS_HOST", "").strip()
user = os.environ.get("SANO_VPS_USER", "root").strip()
password = os.environ.get("SSH_PASS", "").strip()
if not host or not password:
    sys.exit("Missing SANO_VPS_HOST or SSH_PASS")

ssh = paramiko.SSHClient()
ssh.load_system_host_keys()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, password=password, timeout=45)
_, stdout, stderr = ssh.exec_command("systemctl restart short-telegram-bot-lite && systemctl is-active short-telegram-bot-lite", timeout=60)
print(stdout.read().decode())
err = stderr.read().decode()
if err:
    print(err, file=sys.stderr)
ssh.close()

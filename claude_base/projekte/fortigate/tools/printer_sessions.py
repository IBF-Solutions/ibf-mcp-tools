#!/usr/bin/env python3
"""Interactive shell: filter + list active sessions for the Canon printer."""
import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("10.10.40.1", port=10022, username="audit", password="audit",
            timeout=15, banner_timeout=15, look_for_keys=False, allow_agent=False)

shell = ssh.invoke_shell()
time.sleep(0.5)
# Drain banner
while shell.recv_ready():
    shell.recv(4096)

cmds = [
    "config global",  # Ensure global context
    "diagnose sys session filter clear",
    "diagnose sys session filter src 10.10.40.225",
    "diagnose sys session list | grep -E 'hook|policy_id|gwy|state|proto_state'",
]

# Some FGTs don't have config global on this VDOM, so just run the rest
for cmd in [
    "diagnose sys session filter clear",
    "diagnose sys session filter src 10.10.40.225",
    "diagnose sys session list",
]:
    shell.send(cmd + "\n")
    time.sleep(2)
    out = b""
    while shell.recv_ready():
        out += shell.recv(65536)
        time.sleep(0.2)
    print(f"# CMD: {cmd}")
    text = out.decode('utf-8', errors='ignore')
    # Send space repeatedly to drain --More-- prompts
    for _ in range(20):
        if "--More--" in text:
            shell.send(" ")
            time.sleep(0.5)
            while shell.recv_ready():
                more = shell.recv(65536)
                text += more.decode('utf-8', errors='ignore')
        else:
            break
    print(text)
    print("=" * 80)

ssh.close()

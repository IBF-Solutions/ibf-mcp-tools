#!/usr/bin/env python3
"""
FortiGate SSH Connection Manager
Persistent SSH connection handler for running multiple commands on FortiGate devices.
"""

import paramiko
import sys
import time
from typing import Optional, Tuple


class FortiGateConnection:
    """Manages persistent SSH connection to FortiGate device."""

    def __init__(self, host: str, port: int = 10022, username: str = "audit", password: str = "audit"):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ssh = None
        self.connected = False

    def connect(self) -> bool:
        """Establish SSH connection."""
        try:
            if self.connected:
                return True

            print(f"[*] Connecting to {self.host}:{self.port}...")
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            self.ssh.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=15,
                banner_timeout=15,
                look_for_keys=False,
                allow_agent=False
            )
            self.connected = True
            print("[+] Connected!")
            return True

        except paramiko.AuthenticationException as e:
            print(f"[-] Authentication failed: {e}")
            return False
        except Exception as e:
            print(f"[-] Connection error: {e}")
            return False

    def execute(self, command: str) -> Tuple[bool, str]:
        """
        Execute a single command on the FortiGate device.
        Returns (success, output) tuple.
        """
        if not self.connected:
            if not self.connect():
                return False, "Failed to connect"

        try:
            stdin, stdout, stderr = self.ssh.exec_command(command)
            output = stdout.read().decode('utf-8', errors='ignore')
            error = stderr.read().decode('utf-8', errors='ignore')

            if error and not output:
                return False, error
            return True, output

        except Exception as e:
            print(f"[-] Command execution error: {e}")
            self.connected = False
            return False, str(e)

    def disconnect(self) -> None:
        """Close SSH connection."""
        if self.ssh and self.connected:
            try:
                self.ssh.close()
                self.connected = False
                print("[*] Disconnected")
            except Exception as e:
                print(f"[-] Disconnect error: {e}")

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()


def main():
    """CLI interface for FortiGate connection."""
    import argparse

    parser = argparse.ArgumentParser(
        description="FortiGate SSH Connection Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python forti-connect.py "get system status"
  python forti-connect.py "get system interface" --host 10.10.40.1
  python forti-connect.py --interactive
        """
    )

    parser.add_argument("command", nargs="?", help="Command to execute")
    parser.add_argument("--host", default="10.10.40.1", help="FortiGate IP address (default: 10.10.40.1)")
    parser.add_argument("--port", type=int, default=10022, help="SSH port (default: 10022)")
    parser.add_argument("--user", default="audit", help="Username (default: audit)")
    parser.add_argument("--password", default="audit", help="Password (default: audit)")
    parser.add_argument("-i", "--interactive", action="store_true", help="Interactive mode - run multiple commands")

    args = parser.parse_args()

    with FortiGateConnection(args.host, args.port, args.user, args.password) as conn:
        if args.interactive:
            # Interactive mode
            print("\n[*] Interactive mode. Type 'exit' to quit.\n")
            while True:
                try:
                    cmd = input("forti> ").strip()
                    if not cmd:
                        continue
                    if cmd.lower() in ["exit", "quit"]:
                        break

                    success, output = conn.execute(cmd)
                    if success:
                        print(output)
                    else:
                        print(f"[-] Error: {output}")

                except KeyboardInterrupt:
                    print("\n[*] Interrupted")
                    break
                except Exception as e:
                    print(f"[-] Error: {e}")

        elif args.command:
            # Single command mode
            success, output = conn.execute(args.command)
            if success:
                print(output)
                sys.exit(0)
            else:
                print(f"[-] Error: {output}", file=sys.stderr)
                sys.exit(1)

        else:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()

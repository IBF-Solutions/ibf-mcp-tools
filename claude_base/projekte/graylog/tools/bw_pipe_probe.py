#!/usr/bin/env python3
"""Probe the Bitwarden pipe — send handshake, print raw response bytes."""
import base64
import json
import os
import struct
import sys
import win32file

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

def find_pipe():
    pipes = os.listdir("\\\\.\\pipe\\")
    matches = [p for p in pipes if p.endswith(".bw")]
    if not matches:
        sys.exit("Keine .bw-Pipe gefunden")
    print(f"Pipe: {matches[0]}")
    return f"\\\\.\\pipe\\{matches[0]}"

def main():
    pipe_path = find_pipe()
    handle = win32file.CreateFile(
        pipe_path,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        0, None, win32file.OPEN_EXISTING, 0, None
    )
    print("Verbunden.")

    # RSA keypair
    private_key = rsa.generate_private_key(65537, 2048, default_backend())
    pub_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_b64 = base64.b64encode(pub_der).decode()

    msg = json.dumps({
        "command": "bw-handshake",
        "payload": {"publicKey": pub_b64, "applicationName": "test"}
    }).encode()

    # Sende mit 4-byte LE length prefix
    print(f"Sende {len(msg)} Bytes Handshake...")
    win32file.WriteFile(handle, struct.pack("<I", len(msg)) + msg)
    print("Gesendet. Warte auf Antwort (10s Timeout)...")

    # Lese rohe Bytes
    import win32event
    try:
        hr, data = win32file.ReadFile(handle, 4096)
        print(f"Empfangen {len(data)} Bytes:")
        print(f"  hex:  {data.hex()}")
        try:
            print(f"  text: {data.decode('utf-8', errors='replace')}")
        except Exception:
            pass
    except Exception as e:
        print(f"ReadFile Fehler: {e}")

    win32file.CloseHandle(handle)

if __name__ == "__main__":
    main()

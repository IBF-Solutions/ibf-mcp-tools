#!/usr/bin/env python3
"""Bitwarden Desktop IPC test client.

Connects to the Bitwarden Desktop named pipe, performs the RSA handshake,
waits for user approval in the desktop app, then retrieves a credential.

Usage:
    python bw_ipc_test.py
    python bw_ipc_test.py --uri https://gld.ibf-solutions.com
"""
import argparse
import json
import os
import struct
import sys

import win32file
import win32pipe

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend


APP_NAME = "graylog-query-tool"
PIPE_SUFFIX = ".bw"


# ----- pipe discovery --------------------------------------------------------

def find_pipe():
    """Find the Bitwarden named pipe (randomised name ending in .bw)."""
    try:
        pipes = os.listdir("\\\\.\\pipe\\")
    except Exception as e:
        sys.exit(f"[ERROR] Konnte Pipes nicht auflesen: {e}")
    matches = [p for p in pipes if p.endswith(PIPE_SUFFIX)]
    if not matches:
        sys.exit(
            "[ERROR] Keine Bitwarden-Pipe gefunden.\n"
            "Stelle sicher dass:\n"
            "  1. Bitwarden Desktop läuft und entsperrt ist\n"
            "  2. Settings > App Settings > 'Allow browser integration' aktiviert ist"
        )
    if len(matches) > 1:
        print(f"[WARN] Mehrere .bw-Pipes gefunden, nehme erste: {matches[0]}")
    return f"\\\\.\\pipe\\{matches[0]}"


# ----- pipe I/O (LengthDelimitedCodec) ---------------------------------------

def pipe_send(handle, obj):
    """Send a JSON object with 4-byte LE length prefix."""
    data = json.dumps(obj).encode("utf-8")
    header = struct.pack("<I", len(data))
    win32file.WriteFile(handle, header + data)


def pipe_recv(handle):
    """Receive a length-prefixed JSON message."""
    _, header = win32file.ReadFile(handle, 4)
    length = struct.unpack("<I", header)[0]
    _, body = win32file.ReadFile(handle, length)
    return json.loads(body.decode("utf-8"))


# ----- crypto ----------------------------------------------------------------

def generate_rsa_keypair():
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    public_key = private_key.public_key()
    pub_der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    import base64
    return private_key, base64.b64encode(pub_der).decode()


def decrypt_shared_key(private_key, encrypted_b64):
    import base64
    encrypted = base64.b64decode(encrypted_b64)
    return private_key.decrypt(
        encrypted,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None),
    )


def aes_encrypt(key, plaintext):
    import base64, os as _os
    iv = _os.urandom(16)
    cipher = Cipher(algorithms.AES(key[:32]), modes.CBC(iv), backend=default_backend())
    enc = cipher.encryptor()
    # PKCS7 padding
    pad = 16 - len(plaintext) % 16
    padded = plaintext + bytes([pad] * pad)
    ct = enc.update(padded) + enc.finalize()
    return base64.b64encode(iv + ct).decode()


def aes_decrypt(key, ciphertext_b64):
    import base64
    raw = base64.b64decode(ciphertext_b64)
    iv, ct = raw[:16], raw[16:]
    cipher = Cipher(algorithms.AES(key[:32]), modes.CBC(iv), backend=default_backend())
    dec = cipher.decryptor()
    padded = dec.update(ct) + dec.finalize()
    pad = padded[-1]
    return padded[:-pad]


# ----- protocol --------------------------------------------------------------

def handshake(handle):
    """Perform RSA handshake. Returns the AES session key bytes."""
    private_key, pub_b64 = generate_rsa_keypair()

    pipe_send(handle, {
        "command": "bw-handshake",
        "payload": {
            "publicKey": pub_b64,
            "applicationName": APP_NAME,
        }
    })

    print("Warte auf Bestätigung in Bitwarden Desktop...")
    print("(Klicke im Bitwarden-Fenster auf 'Approve' / 'Bestätigen')\n")

    resp = pipe_recv(handle)
    if not resp.get("status"):
        sys.exit(f"[ERROR] Handshake abgelehnt: {resp}")

    shared_key = decrypt_shared_key(private_key, resp["sharedKey"])
    print("[OK] Handshake erfolgreich, Session-Key erhalten.")
    return shared_key


def get_status(handle, session_key):
    payload = json.dumps({"command": "bw-status"}).encode()
    pipe_send(handle, {"command": "bw-status-response",
                       "payload": aes_encrypt(session_key, payload)})
    resp = pipe_recv(handle)
    if resp.get("payload"):
        return json.loads(aes_decrypt(session_key, resp["payload"]))
    return resp


def retrieve_credential(handle, session_key, uri):
    """Request credentials matching a URI."""
    payload = json.dumps({
        "command": "bw-credential-retrieval",
        "payload": {"uri": uri}
    }).encode()
    pipe_send(handle, {
        "command": "bw-credential-retrieval",
        "payload": aes_encrypt(session_key, payload)
    })
    resp = pipe_recv(handle)
    if resp.get("payload"):
        return json.loads(aes_decrypt(session_key, resp["payload"]))
    return resp


# ----- main ------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Bitwarden Desktop IPC Test")
    p.add_argument("--uri", default="https://gld.ibf-solutions.com",
                   help="URI des gesuchten Eintrags (default: https://gld.ibf-solutions.com)")
    args = p.parse_args()

    pipe_path = find_pipe()
    print(f"Pipe gefunden: {pipe_path}")

    handle = win32file.CreateFile(
        pipe_path,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        0, None,
        win32file.OPEN_EXISTING,
        0, None
    )

    try:
        session_key = handshake(handle)
        print(f"\nSuche Eintrag für URI: {args.uri}")
        result = retrieve_credential(handle, session_key, args.uri)
        print(f"\nAntwort: {json.dumps(result, indent=2)}")
    finally:
        win32file.CloseHandle(handle)


if __name__ == "__main__":
    main()

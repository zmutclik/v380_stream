#!/usr/bin/env python3
"""
v380_stream.py - Generic V380 firmware v31/v32 stream client & decryptor.
Dukungan konfigurasi: CLI args > Environment variables > .env file.
Output: raw H.265/HEVC bitstream (stdout '-' atau file path).
"""

import os
import sys
import time
import socket
import struct
import string
import random
import shutil
import argparse
import subprocess
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

STATIC_KEY = b"macrovideo+*#!^@"
# ponytail: key suffix diambil dari libhsMediaLibrary.so v32. Upgrade jika kamera beda vendor pakai suffix lain.
KEY_SUFFIX = bytes.fromhex("5c79142c46238161f00d8082")


def load_dotenv(filepath: str = ".env"):
    """Parser .env minimal stdlib tanpa dependensi external."""
    if not os.path.isfile(filepath):
        return
    with open(filepath, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            os.environ.setdefault(k, v)


def encrypt_password(password: str) -> bytes:
    chars = string.ascii_letters + string.digits
    rk = "".join(random.choices(chars, k=16)).encode()
    pad = password.encode().ljust(48, b"\x00")

    c1 = Cipher(algorithms.AES(STATIC_KEY), modes.ECB()).encryptor()
    mid = c1.update(pad) + c1.finalize()

    c2 = Cipher(algorithms.AES(rk), modes.ECB()).encryptor()
    enc = c2.update(mid) + c2.finalize()
    return rk + enc


def auth(ip: str, port: int, dev_id: int, user: str, password: str, timeout: float = 5.0) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((ip, port))

    pw_enc = encrypt_password(password)
    user_bytes = user.encode()

    pkt = bytearray(520)
    struct.pack_into("<I", pkt, 0, 1167)
    struct.pack_into("<I", pkt, 4, 120)
    pkt[8] = 31
    struct.pack_into("<I", pkt, 9, 10)
    struct.pack_into("<I", pkt, 13, dev_id)
    pkt[49:49 + len(user_bytes)] = user_bytes
    pkt[81:81 + len(pw_enc)] = pw_enc

    s.sendall(pkt)
    resp = s.recv(1024)
    s.close()

    if len(resp) < 20:
        raise RuntimeError(f"Auth response too short: {len(resp)} bytes")

    cmd, res = struct.unpack_from("<II", resp, 0)
    if res != 1001:
        err_map = {1011: "Invalid username", 1012: "Invalid password", 1018: "Invalid device id"}
        raise RuntimeError(f"Auth failed. Code: {res} ({err_map.get(res, 'Unknown error')})")

    ticket = struct.unpack_from("<I", resp, 13)[0]
    return ticket


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed")
        buf.extend(chunk)
    return bytes(buf)


def decrypt_frame(payload: bytearray, cipher: Cipher) -> bytes:
    dec = cipher.decryptor()
    for offset in range(0, len(payload) - 63, 80):
        payload[offset:offset + 64] = dec.update(bytes(payload[offset:offset + 64]))

    for i in range(min(16, len(payload) - 3)):
        if payload[i:i + 3] == b"\x00\x00\x01":
            sc_pos = i if i == 0 or payload[i - 1] != 0 else i - 1
            return bytes(payload[sc_pos:])
        if payload[i:i + 4] == b"\x00\x00\x00\x01":
            return bytes(payload[i:])
    return bytes(payload)


def capture_one_iframe(ip: str, port: int, dev_id: int, ticket: int, quality: int) -> bytes:
    """Ambil tepat 1 I-Frame (keyframe) dari kamera."""
    aes_key = struct.pack("<I", ticket) + KEY_SUFFIX
    cipher = Cipher(algorithms.AES(aes_key), modes.ECB())

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.settimeout(10.0)
    s.connect((ip, port))

    cmd301 = bytearray(256)
    struct.pack_into("<I", cmd301, 0, 301)
    struct.pack_into("<I", cmd301, 4, dev_id)
    struct.pack_into("<I", cmd301, 8, 0)
    struct.pack_into("<H", cmd301, 12, 20)
    struct.pack_into("<I", cmd301, 14, ticket)
    struct.pack_into("<I", cmd301, 22, 4096)  # Audio OFF
    struct.pack_into("<I", cmd301, 26, quality)
    s.sendall(cmd301)
    recv_exact(s, 32)

    cmd303 = bytearray(256)
    struct.pack_into("<I", cmd303, 0, 303)
    struct.pack_into("<H", cmd303, 4, 0x3001)
    s.sendall(cmd303)

    frags = []
    total_expected = 0
    try:
        while True:
            hdr = recv_exact(s, 12)
            if hdr[0] != 0x7f:
                continue
            ftype = hdr[1]
            total_frag, cur_frag, plen = struct.unpack_from("<HHH", hdr, 3)
            pdata = recv_exact(s, plen)

            if ftype in (0x00, 0x28):  # I-Frame
                if cur_frag == 0:
                    frags = []
                    total_expected = total_frag
                frags.append(pdata)

                if cur_frag == total_expected - 1 and len(frags) == total_expected:
                    full = b"".join(frags)
                    payload = bytearray(full[16:])
                    return decrypt_frame(payload, cipher)
    finally:
        s.close()


def find_ffmpeg() -> str:
    """Cari binary ffmpeg di PATH atau direktori fallback."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = [
        os.path.expanduser("~/.local/bin/ffmpeg"),
        os.path.expanduser("~/.pi/agent/bin/ffmpeg"),
        "/tmp/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    raise FileNotFoundError(
        "Binary ffmpeg tidak ditemukan di PATH, ~/.local/bin/ffmpeg, atau /tmp/ffmpeg.\n"
        "Install ffmpeg atau jalankan: export PATH=\"$HOME/.local/bin:$PATH\""
    )


def save_snapshot(ip: str, port: int, dev_id: int, ticket: int, quality: int, out_path: str):
    sys.stderr.write(f"[V380] Capturing keyframe for snapshot -> {out_path}...\n")
    iframe_data = capture_one_iframe(ip, port, dev_id, ticket, quality)

    ext = os.path.splitext(out_path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png"):
        ffmpeg_bin = find_ffmpeg()
        proc = subprocess.run(
            [ffmpeg_bin, "-y", "-loglevel", "error", "-f", "hevc", "-i", "-", "-vframes", "1", out_path],
            input=iframe_data,
            check=True
        )
    else:
        with open(out_path, "wb") as f:
            f.write(iframe_data)

    sys.stderr.write(f"[V380] Snapshot saved successfully to {out_path}\n")


def stream_loop(ip: str, port: int, dev_id: int, ticket: int, quality: int, out_file):
    aes_key = struct.pack("<I", ticket) + KEY_SUFFIX
    cipher = Cipher(algorithms.AES(aes_key), modes.ECB())

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.settimeout(10.0)
    s.connect((ip, port))

    # Cmd 301 - Login Stream
    cmd301 = bytearray(256)
    struct.pack_into("<I", cmd301, 0, 301)
    struct.pack_into("<I", cmd301, 4, dev_id)
    struct.pack_into("<I", cmd301, 8, 0)
    struct.pack_into("<H", cmd301, 12, 20)
    struct.pack_into("<I", cmd301, 14, ticket)
    struct.pack_into("<I", cmd301, 22, 4097)  # Audio: 4097=on, 4096=off
    struct.pack_into("<I", cmd301, 26, quality)  # Quality: 1=HD, 0=SD
    s.sendall(cmd301)

    resp401 = recv_exact(s, 32)
    cmd401, res401 = struct.unpack_from("<II", resp401, 0)
    if res401 != 1001:
        s.close()
        raise RuntimeError(f"Stream login failed: {res401}")

    # Cmd 303 - Start Stream
    cmd303 = bytearray(256)
    struct.pack_into("<I", cmd303, 0, 303)
    struct.pack_into("<H", cmd303, 4, 0x3001)
    s.sendall(cmd303)

    sys.stderr.write("[V380] Streaming started.\n")
    sys.stderr.flush()

    frags = []
    total_expected = 0

    while True:
        hdr = recv_exact(s, 12)
        if hdr[0] != 0x7f:
            continue

        ftype = hdr[1]
        total_frag, cur_frag, plen = struct.unpack_from("<HHH", hdr, 3)
        if plen == 0 or plen > 20000:
            continue

        pdata = recv_exact(s, plen)

        # Video frame types: 0x00=I, 0x01=P (v30-), 0x28=I, 0x29=P (v31+)
        if ftype in (0x00, 0x01, 0x28, 0x29):
            if cur_frag == 0:
                frags = []
                total_expected = total_frag
            frags.append(pdata)

            if cur_frag == total_expected - 1 and len(frags) == total_expected:
                full = b"".join(frags)
                frags.clear()
                if len(full) < 16:
                    continue

                # Strip 16-byte inner metadata (frameId, frameType, frameRate, ts)
                payload = bytearray(full[16:])

                # Decrypt: first 64 bytes of every 80-byte stride
                dec = cipher.decryptor()
                for offset in range(0, len(payload) - 63, 80):
                    payload[offset:offset + 64] = dec.update(bytes(payload[offset:offset + 64]))

                # Locate Annex-B start code
                sc_pos = -1
                for i in range(min(16, len(payload) - 3)):
                    if payload[i:i + 3] == b"\x00\x00\x01":
                        sc_pos = i if i == 0 or payload[i - 1] != 0 else i - 1
                        break
                    if payload[i:i + 4] == b"\x00\x00\x00\x01":
                        sc_pos = i
                        break

                if sc_pos >= 0:
                    out_file.write(payload[sc_pos:])
                    out_file.flush()


def main():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(env_path)

    parser = argparse.ArgumentParser(description="Generic V380 Stream Client & Decryptor")
    parser.add_argument("--ip", default=os.getenv("V380_IP"), help="Camera IP (default: V380_IP env)")
    parser.add_argument("--port", type=int, default=int(os.getenv("V380_PORT", "8800")), help="Camera port")
    parser.add_argument("--id", type=int, default=int(os.getenv("V380_DEVICE_ID", "0")) if os.getenv("V380_DEVICE_ID") else None, help="Device ID")
    parser.add_argument("--user", default=os.getenv("V380_USERNAME"), help="Username")
    parser.add_argument("--password", default=os.getenv("V380_PASSWORD"), help="Password")
    parser.add_argument("--quality", type=int, default=int(os.getenv("V380_QUALITY", "1")), choices=[0, 1], help="1=HD, 0=SD")
    parser.add_argument("--output", default=os.getenv("V380_OUTPUT", "-"), help="Output file path or '-' for stdout")
    parser.add_argument("--snapshot", metavar="FILE", help="Capture single image (e.g. snap.jpg) then exit")
    args = parser.parse_args()

    # Validasi parameter wajib
    missing = [name for name, val in [("ip", args.ip), ("id", args.id), ("user", args.user), ("password", args.password)] if not val]
    if missing:
        parser.error(f"Parameter wajib belum diset: {', '.join(missing)}. Set via CLI atau file .env")

    if args.snapshot:
        ticket = auth(args.ip, args.port, args.id, args.user, args.password)
        save_snapshot(args.ip, args.port, args.id, ticket, args.quality, args.snapshot)
        return

    out_file = sys.stdout.buffer if args.output == "-" else open(args.output, "wb")

    while True:
        try:
            sys.stderr.write(f"[V380] Authenticating to {args.ip}:{args.port} (ID: {args.id}, User: {args.user})...\n")
            ticket = auth(args.ip, args.port, args.id, args.user, args.password)
            sys.stderr.write(f"[V380] Auth OK (ticket: {ticket}). Starting stream...\n")
            stream_loop(args.ip, args.port, args.id, ticket, args.quality, out_file)
        except KeyboardInterrupt:
            sys.stderr.write("\n[V380] Stopped by user.\n")
            break
        except Exception as e:
            sys.stderr.write(f"[V380] Error: {e}. Reconnecting in 3 seconds...\n")
            time.sleep(3)


if __name__ == "__main__":
    main()

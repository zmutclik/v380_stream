# Dokumentasi Akses & RTSP Stream Kamera V380 (Firmware v32)

Dokumentasi teknis ekstraksi video dan pembuatan RTSP bridge untuk kamera V380 / Macrovideo generasi firmware v31 ke atas (terenkripsi AES-128-ECB).

---

## 1. Kredensial & Parameter Kamera

| Parameter | Nilai | Catatan |
|---|---|---|
| **IP Kamera** | `192.168.1.100` | Port TCP: `8800` (Data & Stream) |
| **Device ID** | `12345678` | Integer 32-bit LE |
| **Username** | `admin` | Truncated/akun registrasi di cloud Macrovideo |
| **Password** | `password123` | Plaintext (dienkripsi 2 lapis AES) |
| **Firmware** | `deviceVersion = 32` | Format paket v31/v32 (520 bytes) |
| **Codec Video** | `H.265 / HEVC` | 2304 x 2592 px @ 16 fps |

---

## 2. Cara Kerja Protokol (Reverse-Engineered)

### A. Autentikasi (`cmd 1167` -> `cmd 1168`)
1. Password di-pad menjadi 48 byte (zero padding).
2. Enkripsi Lapis 1: AES-128-ECB dengan static key `macrovideo+*#!^@`.
3. Enkripsi Lapis 2: AES-128-ECB dengan random key 16-byte alfabetik.
4. Payload auth dikirim dalam paket 520 byte ke port 8800:
   - Byte 0..3: `1167` (uint32 LE)
   - Byte 4..7: `120` (uint32 LE)
   - Byte 8: `31` (version byte)
   - Byte 9..12: `10` (uint32 LE)
   - Byte 13..16: Device ID (`12345678`)
   - Byte 49..80: Username (`admin`)
   - Byte 81..144: Random Key (16B) + Ciphertext Lapis 2 (48B) = 64 byte.
5. Kamera membalas `cmd 1168` dengan `loginResult = 1001` (sukses) dan menyertakan `authTicket` (uint32 LE) di byte 13..16.

### B. Login Stream (`cmd 301` -> `cmd 401`)
1. Buka koneksi TCP baru ke port `8800`.
2. Kirim paket 256 byte:
   - Byte 0..3: `301` (uint32 LE)
   - Byte 4..7: Device ID
   - Byte 12..13: `20` (uint16 LE)
   - Byte 14..17: `authTicket`
   - Byte 22..25: `4097` (Audio ON)
   - Byte 26..29: `1` (Kualitas HD) atau `0` (SD)
3. Kamera membalas `cmd 401` dengan konfirmasi ukuran resolusi (`2304x2592`).

### C. Start Stream (`cmd 303`)
1. Kirim paket 256 byte:
   - Byte 0..3: `303` (uint32 LE)
   - Byte 4..5: `0x3001` (uint16 LE)
2. Kamera langsung mengirim fragmen frame raw secara terus menerus.

### D. Dekripsi Video (AES-128-ECB)
- Sesi streaming firmware v31+ dienkripsi dengan kunci AES 16-byte:
  ```
  AES_KEY = authTicket (4-byte LE) + 0x5c79142c46238161f00d8082 (12 byte)
  ```
- Pola enkripsi frame H.265:
  - Setiap blok 80 byte data payload:
    - 64 byte pertama: didekripsi dengan AES-128-ECB.
    - 16 byte sisanya: dibiarkan plaintext.
  - Cari start code Annex-B (`00 00 00 01` atau `00 00 01`) untuk mendapatkan stream NAL unit H.265 murni.

---

## 3. Ekstraktor Video Generik (`v380_stream.py`)

File skrip: `./v380_stream.py`

Skrip mendukung hierarki konfigurasi:
1. Argumen baris perintah (`--ip`, `--id`, dll.)
2. Environment variables (`V380_IP`, dll.)
3. File `.env` (di-load otomatis menggunakan parser internal stdlib, tanpa dependensi eksternal).

### File `.env`:
```ini
V380_IP=192.168.1.100
V380_PORT=8800
V380_DEVICE_ID=12345678
V380_USERNAME=admin
V380_PASSWORD=password123
V380_QUALITY=1
V380_OUTPUT=-
RTSP_MODE=production # production (senyap) atau debug (verbose)
```

### Contoh Penggunaan CLI:

1. **Ambil 1 Gambar / Snapshot JPG:**
   ```bash
   ./v380_stream.py --snapshot snapshot.jpg
   ```
   *Skrip otomatis connect, ambil 1 keyframe H.265, dekripsi AES, decode via FFmpeg ke JPG, dan disconnect.*

2. **Jalankan streaming (otomatis baca `.env`):**
   ```bash
   ./v380_stream.py
   ```

3. **Simpan stream video ke file `.h265`:**
   ```bash
   ./v380_stream.py --output kamera.h265
   ```

4. **Override konfigurasi via argumen:**
   ```bash
   ./v380_stream.py --ip 192.168.1.50 --id 88888888 --user admin --password rahasia --output -
   ```

---

## 4. Cara Menjalankan RTSP Server (MediaMTX)

### Prasyarat (Pilih salah satu):
- **Opsi A (Docker - Paling Mudah):** Cukup Docker & Docker Compose terpasang.
- **Opsi B (Native Linux):**
  - Python 3 + lib: `pip install cryptography`
  - Binary `ffmpeg` dan `mediamtx` (bisa di PATH atau `~/.local/bin/`).

### Langkah Cepat (Quick Start):

1. **Salin template config:**
   ```bash
   cp .env.example .env
   # Edit .env sesuaikan IP, Device ID, username, password kamera
   nano .env
   ```

2. **Jalankan via Docker Compose:**
   ```bash
   docker compose up -d
   docker compose logs -f
   ```

3. **Atau jalankan native:**
   ```bash
   # Mode production (log minimal/warning only)
   ./run_rtsp.sh

   # Mode debug (log verbose)
   ./run_rtsp.sh debug
   ```

Output URL aktif:
- **RTSP URL** (VLC, OBS, NVR, Frigate, Home Assistant):
  ```
  rtsp://<HOST_IP>:8554/v380
  ```
- **WebRTC URL** (buka langsung di browser HP/PC tanpa aplikasi):
  ```
  http://<HOST_IP>:8889/v380
  ```
- **HLS URL** (Safari / web video player):
  ```
  http://<HOST_IP>:8888/v380
  ```

### Jalankan via Docker Compose (Background / Auto-restart):
```bash
docker compose up -d
docker compose logs -f
```

### Jalankan di Background (systemd / nohup):
```bash
nohup ./run_rtsp.sh > /tmp/rtsp.log 2>&1 &
```

---

## 5. Live Preview Langsung ke Player (Tanpa Server)

Jika hanya ingin melihat video langsung di desktop Linux:

```bash
# Menggunakan MPV
./v380_stream.py --output - | mpv --demuxer-lavf-format=hevc -

# Menggunakan VLC
./v380_stream.py --output - | cvlc --demux=hevc -
```

---

## 6. Daftar File Project

| File | Fungsi |
|---|---|
| `v380_stream.py` | Engine autentikasi, dekripsi AES stream H.265 & snapshot |
| `mediamtx.yml` | Konfigurasi server MediaMTX untuk RTSP/WebRTC bridge |
| `run_rtsp.sh` | Runner script server RTSP satu perintah |
| `Dockerfile` | Image container Alpine + MediaMTX + FFmpeg |
| `compose.yaml` | Orkestrasi Docker container (host network) |
| `.env.example` | Template konfigurasi kredensial |
| `.env` | File kredensial aktif (diabaikan git) |
| `LICENSE` | Lisensi open source (MIT) |

---

## Lisensi

[MIT License](LICENSE)



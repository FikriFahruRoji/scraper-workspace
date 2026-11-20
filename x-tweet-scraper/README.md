> **Pembaruan 14 November 2026:** jalur pengambilan default kini memakai
> browser `scrapling.fetchers.StealthySession` (stealth), termasuk permintaan
> endpoint Play Store, Yahoo Finance, dan YouTube. Instruksi/uraian mode HTTP
> lama di bawah merupakan dokumentasi sebelum migrasi; lihat
> [panduan stealth bersama](../STEALTH_MODE.md) untuk konfigurasi terbaru.

# X (Twitter) Tweet Scraper

Scraping tweet dari **X / Twitter** berdasarkan **kata kunci** dan **rentang waktu**,
menggunakan library [Scrapling](https://github.com/D4Vinci/Scrapling) + **Python**,
dengan antarmuka **Streamlit**.

## Fitur

- Cari tweet berdasarkan kata kunci + rentang tanggal (operator `since:` / `until:`).
- Pilihan urutan **Terbaru** (`live`) atau **Terpopuler** (`top`).
- Login manual ke X lewat browser (satu kali); **sesi tersimpan** di `sessions/x_profile`
  sehingga tidak perlu login ulang.
- Mode headless / Google Chrome asli (lebih sulit terdeteksi).
- **Stealth penuh**: `StealthySession` dengan `hide_canvas`, `block_webrtc`, WebGL
  aktif, locale `en-US`, dan zona waktu `Asia/Jakarta` di semua jalur (login,
  cek sesi, dan scraping).
- **Log proses tampil langsung** saat scraping berjalan.
- **Jeda 30 detik setiap 2 menit** selama proses scraping untuk mengurangi risiko rate-limit X.
- Tanpa batas jumlah tweet (berhenti saat hasil habis; bisa diberi batas waktu opsional).
- Kolom lengkap: `waktu (GMT+7)`, `nama`, `username`, `VERIFIED`, `text`, `bahasa`,
  `IN_REPLY_TO`, `IN_REPLY_TO_URL`, `VIEW_COUNT`, `QUOTE_COUNT`, `REPLY_COUNT`,
  `RETWEET_COUNT`, `FAVORITE_COUNT`, `USER_LOCATION`, `TWEET URL`.
- Hasil ditampilkan sebagai tabel + metrik ringkas + grafik ringkasan.
- Ekspor hasil ke **CSV** dan **JSON**.

## Persyaratan

- **Python 3.10+** (diuji dengan 3.11)
- Google Chrome terpasang (untuk mode anti-deteksi) — opsional, ada cadangan Chromium.

## Instalasi

```bash
cd x-tweet-scraper

# 1. Buat virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 2. Install dependensi
pip install -r requirements.txt

# 3. (Opsional) install browser cadangan Chromium
scrapling install --force
```

## Menjalankan

### Cara cepat (macOS)

Klik dua kali file **`Jalankan X Tweet Scraper.command`** di folder proyek.
Terminal akan terbuka, lingkungan virtual dicek/dibuat otomatis, lalu aplikasi
terbuka di browser. Tutup jendela Terminal untuk menghentikan aplikasi.

> Pertama kali: jika macOS memblokir, klik kanan file → **Open**, atau jalankan
> `chmod +x "Jalankan X Tweet Scraper.command"` sekali.

### Cara manual

```bash
source .venv/bin/activate
streamlit run app.py
```

Lalu buka URL yang muncul (biasanya http://localhost:8501).

### Alur pakai

1. Di sidebar, klik **🔑 Login / Refresh sesi**.
   Sebuah jendela browser akan terbuka — login ke akun X Anda di sana.
   Setelah berhasil login, aplikasi mendeteksinya otomatis dan menyimpan sesi.
2. Isi **kata kunci** dan **rentang waktu**.
3. Atur opsi (urutan, jeda scroll, batas waktu opsional), lalu klik **🚀 Mulai Scraping**.
   **Log proses tampil langsung** di halaman saat scraping berjalan.
4. Hasil muncul di area utama; unduh lewat tombol **CSV** / **JSON**.

## Catatan penting

- **UI baru X (2026) didukung.** Scraper menangani endpoint `SearchByRawQuery`
  dan skema `details.full_text` / `details.created_at_ms` / `counts.*`, sekaligus
  tetap kompatibel dengan skema lama (`legacy.full_text`, `SearchTimeline`).
- **Patuhi aturan & ToS X.** Gunakan hanya untuk konten yang boleh Anda akses.
  Tambahkan jeda antar-scroll (opsi lanjutan) dan batasi volume agar tidak membebani server.
- Operator `until:` di X bersifat eksklusif; kode otomatis menambah 1 hari agar
  tanggal akhir ikut tersertakan.
- Filter tanggal juga diterapkan ulang pada hasil (menggunakan waktu **GMT+7**).
- Sesi login disimpan di folder `sessions/x_profile` — jangan bagikan folder ini
  karena berisi cookie sesi akun Anda.

## Struktur proyek

```
x-tweet-scraper/
├── Jalankan X Tweet Scraper.command  # Launcher klik-dua-kali (macOS)
├── app.py            # Antarmuka Streamlit
├── scraper.py        # Logika scraping (Scrapling)
├── requirements.txt
├── README.md
└── sessions/         # Profil browser persisten (login X)
```

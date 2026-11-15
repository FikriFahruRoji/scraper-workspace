> **Pembaruan 14 November 2026:** jalur pengambilan default kini memakai
> browser `scrapling.fetchers.StealthySession` (stealth), termasuk permintaan
> endpoint Play Store, Yahoo Finance, dan YouTube. Instruksi/uraian mode HTTP
> lama di bawah merupakan dokumentasi sebelum migrasi; lihat
> [panduan stealth bersama](../STEALTH_MODE.md) untuk konfigurasi terbaru.

# Threads Scraper

Scraping post dari **Threads (threads.com)** berdasarkan **kata kunci** dan
**rentang waktu**, menggunakan library [Scrapling](https://github.com/D4Vinci/Scrapling)
+ **Python**, dengan antarmuka **Streamlit**.

Proyek ini mengikuti konsep yang sama dengan proyek `x-tweet-scraper`:
login manual sekali, sesi browser persisten, scraping di background, log live,
tabel hasil, ringkasan, dan ekspor CSV/JSON.

> ## ⚠️ Baca sebelum memakai
>
> Saat proyek ini dibuat, `https://www.threads.com/robots.txt` berisi
> `User-agent: *` → `Disallow: /`, dan halaman itu menyatakan bahwa
> pengumpulan data lewat cara otomatis dilarang tanpa izin tertulis dari
> Threads/Meta.
>
> Aplikasi ini **tidak** melewati login, CAPTCHA, paywall, atau kontrol akses,
> dan tidak memakai proxy untuk menyamarkan diri. Aplikasi hanya membuka
> halaman pencarian seperti browser biasa memakai sesi akun Anda.
>
> Gunakan hanya jika Anda memiliki izin/otorisasi yang sesuai, hanya untuk
> konten publik, dan patuhi ToS serta hukum yang berlaku. Jika tidak punya
> izin, gunakan Threads API resmi atau minta data langsung ke Meta.

## Fitur

- Cari post Threads berdasarkan kata kunci/frasa/hashtag.
- Filter **rentang tanggal inklusif** (GMT+7), diterapkan pada waktu post.
- Pilihan urutan **Terbaru** (`filter=recent`) atau **Terpopuler** (`top`).
- Login manual ke Threads/Instagram lewat browser (sekali); **sesi tersimpan**
  di `sessions/threads_profile` sehingga tidak perlu login ulang.
- Mode headless / Google Chrome asli.
- **Log proses tampil langsung** saat scraping berjalan.
- **Jeda 30 detik setiap 2 menit** untuk mengurangi risiko rate-limit.
- Batas waktu dan batas jumlah post yang bisa diatur.
- Parsing dari respons GraphQL internal + data SSR, bukan sekadar teks DOM,
  sehingga metrik suka/balasan/repost ikut terbaca.
- Kolom: `waktu (GMT+7)`, `nama`, `username`, `VERIFIED`, `text`,
  `MEDIA`, `LIKE_COUNT`, `REPLY_COUNT`, `REPOST_COUNT`, `QUOTE_COUNT`,
  `IN_REPLY_TO`, `USER_BIO`, `POST URL`.
- Tabel + metrik ringkas + grafik ringkasan.
- Ekspor hasil ke **CSV** dan **JSON**.

## Persyaratan

- **Python 3.10+** (diuji dengan 3.11)
- Google Chrome terpasang (untuk mode anti-deteksi) — opsional, ada cadangan
  Chromium dari Playwright/Scrapling.

## Instalasi

```bash
cd threads-scraper

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

Klik dua kali file **`Jalankan Threads Scraper.command`** di folder proyek.
Terminal akan terbuka, environment virtual dicek/dibuat otomatis, lalu aplikasi
terbuka di browser. Tutup jendela Terminal untuk menghentikan aplikasi.

> Pertama kali: jika macOS memblokir, klik kanan file → **Open**, atau jalankan
> `chmod +x "Jalankan Threads Scraper.command"` sekali.

### Cara manual

```bash
source .venv/bin/activate
streamlit run app.py
```

Lalu buka URL yang muncul (biasanya http://localhost:8501).

### Alur pakai

1. Di sidebar, klik **🔑 Login / Refresh sesi**.
   Jendela browser akan terbuka — login ke Threads (bisa lewat Instagram).
   Setelah berhasil, aplikasi mendeteksinya otomatis dan menyimpan sesi.
2. Isi **kata kunci** dan **rentang waktu**.
3. Atur opsi (urutan, jeda scroll, batas waktu/post), lalu klik **🚀 Scraping**.
   Log proses tampil langsung di halaman.
4. Hasil muncul di area utama; unduh lewat tombol **CSV** / **JSON**.

## Cara kerja

1. Scrapling membuka `https://www.threads.com/search?q=...&serp_type=default`
   dengan sesi browser persisten.
2. Post awal diambil dari JSON SSR yang tertanam di HTML.
3. Halaman digulir; respons GraphQL internal (`/api/graphql`) ditangkap dan
   diparsing untuk mendapatkan post berikutnya beserta metriknya.
4. Semua post dideduplikasi berdasarkan ID/kode, lalu difilter berdasarkan
   tanggal lokal **GMT+7** dan diurutkan dari yang terbaru.
5. Proses berhenti saat tidak ada post baru beberapa kali, batas waktu/post
   tercapai, atau halaman habis.

## Catatan & batasan

- **Tanpa login**, Threads hanya menampilkan sekitar **24 post pertama** lalu
  memasang ajakan login. Aplikasi tetap bisa menampilkan hasil tersebut, tetapi
  akan memberi peringatan `Hasil dibatasi karena belum login`.
- Threads **tidak punya operator tanggal** seperti `since:`/`until:` di X.
  Karena itu rentang waktu diterapkan pada post yang berhasil dikumpulkan.
  Untuk rentang lama, naikkan batas waktu dan pastikan urutan **Terbaru**.
- Parameter `filter=recent` dapat diabaikan Threads pada kondisi tertentu;
  filter tanggal di sisi klien tetap dijalankan.
- Hasil bisa kosong jika Threads sedang membatasi permintaan, halaman berubah,
  atau akun belum login.
- Sesi login disimpan di `sessions/threads_profile` — **jangan bagikan folder
  ini** karena berisi cookie sesi akun Anda.
- Gunakan jeda antar-scroll dan batas volume yang wajar. Jangan menarik data
  pribadi/sensitif.

## Struktur proyek

```
threads-scraper/
├── Jalankan Threads Scraper.command  # Launcher klik-dua-kali (macOS)
├── app.py                 # Antarmuka Streamlit
├── scraper.py             # Logika scraping (Scrapling)
├── requirements.txt
├── README.md
├── .streamlit/
│   └── config.toml
└── sessions/              # Profil browser persisten (login Threads)
```

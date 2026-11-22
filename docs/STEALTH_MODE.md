# Menjalankan aplikasi dengan Scrapling StealthySession

Aplikasi memakai Scrapling 0.5.1 dan Streamlit 1.67.0. Jalankan lewat launcher di
folder proyek (`Jalankan Scraper Workspace.command` di macOS, `.bat` di Windows),
atau dari folder proyek:

```bash
.venv/bin/python -m streamlit run workspace_app.py
```

Untuk instalasi baru, dari folder proyek:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/scrapling install --force
```

Play Store dan saham menggunakan Google Chrome yang terpasang. Pada
proyek lain yang menyediakan opsi Chrome, nonaktifkan opsi tersebut untuk
memakai Chromium bawaan Scrapling. Scraper berita memakai Chromium bawaan.

| Proyek | Jalur data |
|---|---|
| Berita | StealthySession untuk semua portal, termasuk IDX Channel |
| Google Maps Review | Endpoint dan pembacaan tampilan melalui StealthySession |
| Google Maps Lokasi | Pencarian daftar tempat dan pembacaan halaman detail melalui StealthySession |
| Instagram, Threads, X | StealthySession dengan profil browser per platform; X menutup canvas/WebRTC |
| Play Store | GET metadata dan POST ulasan melalui browser, parser Play Store 1.2.7 |
| Saham | GET data OHLCV melalui browser |
| YouTube | Jalur endpoint dan jalur tampilan melalui browser |

Jalankan `.venv/bin/python tools/check_transport.py` untuk menguji GET dan POST Chrome
headless ke fixture lokal lewat transport bersama (`scrapers/_shared/stealth_transport.py`).
Pemeriksaan sumber publik dengan volume terbatas tersedia lewat
`.venv/bin/python tools/check_public_stealth.py`.

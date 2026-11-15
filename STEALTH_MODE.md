# Menjalankan aplikasi dengan Scrapling StealthySession

Aplikasi memakai Scrapling 0.5.1 dan Streamlit 1.67.0.
Launcher macOS memakai environment di folder induk jika tersedia, atau
environment lokal bila aplikasi dipindahkan sendiri. Buka launcher dari
folder aplikasi atau jalankan dari folder proyek:

```bash
../.venv/bin/python -m streamlit run app.py
```

Untuk instalasi baru, dari folder induk:

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
| Google Maps | Endpoint dan pembacaan tampilan melalui StealthySession |
| Instagram, Threads, X | StealthySession dengan profil browser per platform |
| Play Store | GET metadata dan POST ulasan melalui browser, parser Play Store 1.2.7 |
| Saham | GET data OHLCV melalui browser |
| YouTube | Jalur endpoint dan jalur tampilan melalui browser |
| Google Scholar dan OpenAlex | Profil lewat StealthySession, publikasi dari impor HTML/CSV |

Jalankan `.venv/bin/python tools/check_stealth.py` untuk menguji startup semua
aplikasi serta GET dan POST Chrome headless ke fixture lokal. Pemeriksaan
sumber publik dengan volume terbatas tersedia lewat
`.venv/bin/python tools/check_public_stealth.py`.

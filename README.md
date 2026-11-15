# Scraper Workspace

Satu aplikasi Streamlit untuk mengumpulkan, mengimpor, dan mengekspor data
dari **10 sumber**. Workspace memakai **Scrapling stealth** untuk jalur
browser dan menyimpan riwayat pekerjaan di SQLite.

| Sumber | Data yang tersedia |
| --- | --- |
| Berita | Artikel dari 10 portal Indonesia |
| Google Maps dan Play Store | Ulasan tempat dan aplikasi |
| Instagram, Threads, dan X | Post, profil, pencarian, atau balasan sesuai sumber |
| YouTube | Komentar video dan balasan |
| Saham | Harga historis OHLCV, dividen, dan split |
| Google Scholar | Publikasi dari impor berkas dan pratinjau profil penulis |
| OpenAlex | Publikasi dari impor berkas dan metrik profil penulis |

## Jalankan di laptop

1. Pasang **Python 3.11** dan siapkan koneksi internet untuk pemasangan awal.
   Di macOS pastikan `python3.11` tersedia. Di Windows aktifkan Python Launcher
   agar perintah `py -3.11` tersedia.
2. Clone repositori atau ekstrak ZIP sumber.
3. Jalankan **`Jalankan Scraper Workspace.command`** di macOS atau
   **`Jalankan Scraper Workspace.bat`** di Windows. Launcher menyiapkan
   lingkungan Python dan memasang Chromium untuk Scrapling.
4. Buka [Scraper Workspace di localhost](http://127.0.0.1:8510).
   Aplikasi lokal hanya mendengarkan di `127.0.0.1`.

Jika macOS meminta menjalankan launcher dari Terminal, gunakan
`bash "Jalankan Scraper Workspace.command"` dari folder proyek. Delapan
scraper awal dan dua sumber publikasi juga punya launcher lokal masing-masing.

## Cara pakai

- Pilih sumber di sidebar, isi parameter, lalu mulai pekerjaan. Satu pekerjaan
  atau login platform bisa aktif pada satu waktu. Pekerjaan tetap berjalan
  ketika berpindah halaman.
- Status dan tombol **Hentikan** tampil di atas halaman. Untuk scraping,
  proses berhenti setelah permintaan yang sedang berjalan selesai. Log dan
  hasil sementara tersedia di halaman sumber dan **Riwayat**.
- Setelah pekerjaan berakhir, unduh seluruh baris tersimpan sebagai **Excel
  (.xlsx)**, **CSV**, atau **JSON**. Pencarian di tabel hanya memfilter tampilan.
  Untuk teks di atas batas Excel 32.767 karakter per sel, gunakan CSV atau JSON.
- Riwayat menyimpan parameter, hasil, status, dan log. Setelah penutupan tiba-tiba,
  pekerjaan menjadi **Terputus** saat aplikasi dibuka kembali. Data sampai
  penyimpanan terakhir tetap tersedia. Anda dapat menghapus pekerjaan yang
  sudah berakhir dari Riwayat.
- Klik **Login platform** jika dibutuhkan oleh sumber. Masuk secara manual lewat
  browser. Workspace memakai profil browser terpisah dari scraper mandiri.

Data Workspace disimpan di `~/Library/Application Support/ScraperWorkspace`
pada macOS atau `%LOCALAPPDATA%\ScraperWorkspace` pada Windows. Penghapusan
riwayat terpisah dari penyimpanan profil login. Berkas riwayat, cookie, profil
browser, dan hasil ekspor tidak diperlukan untuk menjalankan kode dari GitHub.

### Google Scholar dan OpenAlex

Google Scholar membaca HTML hasil pencarian. Untuk profil penulis, gunakan HTML,
CSV ekspor profil, atau tabel HTML yang disalin dari browser. Pengambilan langsung
membaca halaman awal profil publik. OpenAlex membaca daftar publikasi dari HTML
atau CSV, dan dapat mengambil metrik halaman profil dengan Scrapling stealth.
Impor menggunakan berkas yang Anda simpan dari browser. Ekspor Excel Scholar
menempatkan publikasi dan metrik pada tab terpisah jika keduanya tersedia.

## Deploy ke Streamlit

Entrypoint aplikasi adalah **`workspace_app.py`** dan dependensi Python ada di
**`requirements.txt`** di root repositori. Lihat [Panduan deployment](DEPLOYMENT.md)
untuk pemasangan di server Linux dan persiapan Streamlit Community Cloud.

Versi saat ini memiliki **satu antrean, satu riwayat, dan satu set profil browser
per instalasi**. Di server, seluruh pengunjung aplikasi akan berbagi state
tersebut. Gunakan akses khusus operator selama isolasi per pengguna belum ada.

## Uji pengembang

Setelah memasang dependensi dan menyiapkan lingkungan, jalankan:

```bash
python -m pip install pytest
python -m pytest -q tests/test_workspace.py
python tools/check_news_performance.py
```

Uji Workspace mencakup form 10 sumber, pekerjaan tunggal, penghentian, pemulihan
hasil parsial, impor publikasi, dan ekspor. Integrasi browser dan launcher
gabungan telah diuji di macOS. Launcher Windows belum diuji pada perangkat
Windows.

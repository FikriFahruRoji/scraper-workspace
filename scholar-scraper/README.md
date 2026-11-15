# Google Scholar Scraper

Jalankan `Jalankan Google Scholar.command` di macOS atau jalankan
`python -m streamlit run app.py` dari folder ini setelah memasang
`requirements.txt`. Pengambilan profil lewat Scrapling memerlukan browser
yang terpasang melalui `scrapling install` pada environment yang digunakan.

## Kata kunci

1. Isi kata kunci dan batas publikasi. Nilai awalnya 100.
2. Klik **Buka di Google Scholar**. Simpan halaman hasil yang diperlukan
   sebagai HTML dari browser.
3. Unggah berkas HTML sesuai urutan halaman untuk langsung melihat hasil.

## Profil penulis

1. Masukkan tautan profil publik `https://scholar.google.com/citations?user=...`.
2. Untuk melihat halaman awal, klik **Ambil halaman awal profil**. Fitur ini
   memakai Scrapling StealthySession.
3. Untuk seluruh daftar, buka di browser, klik **Show more** sampai habis,
   simpan halaman yang sudah diperluas sebagai HTML, lalu unggah. Aplikasi tidak memberi batas jumlah publikasi profil.

[robots.txt](https://scholar.google.com/robots.txt) melarang pengambilan otomatis
hasil pencarian dan halaman lanjutan profil. Aplikasi mengolah halaman tersebut
dari HTML yang disimpan pengguna.

Untuk profil sendiri, login di Google Scholar, centang **Title**, lalu pilih
**Export → CSV**. Unggah CSV langsung tanpa perlu menempel tautan profil.

## Data

Unduh CSV publikasi atau CSV metrik profil. Ringkasan profil mencakup
total sitasi, h-index, dan i10-index yang ditampilkan. Untuk HTML unggahan,
waktu proses menunjukkan waktu impor. Angka sitasi tetap mengikuti isi HTML.
Jika publikasi yang ditampilkan masih bisa diperluas atau salah satu berkas
gagal dibaca, hasil yang berhasil diproses diberi status parsial.

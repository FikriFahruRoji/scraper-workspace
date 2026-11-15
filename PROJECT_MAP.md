# Peta proyek Scraper

Tanggal pemeriksaan: 14 November 2026 (Asia/Jakarta).

## Cakupan dan batas bukti

Delapan proyek, 21 berkas Python, 15.762 baris Python. Seluruh sumber aplikasi
dibaca, termasuk JavaScript yang ditanam dalam Python, README, requirements,
konfigurasi Streamlit, launcher macOS, dan gitignore. Paket pihak ketiga dalam
`.venv`, cache bytecode, dan gambar preview bukan kode sumber aplikasi.

Semua 21 berkas lolos `ast.parse`; delapan launcher lolos `bash -n`. Beberapa
fungsi parser diperiksa secara offline. Tidak dilakukan instalasi dependensi,
login, scraping langsung, atau pengujian antarmuka. Karena itu kompatibilitas
endpoint dan keberhasilan operasional saat ini belum dibuktikan. Kode aplikasi
tidak diubah. Dokumen ini adalah hasil pembelajaran dan pemeriksaan awal,
bukan audit lengkap.

## Peta fungsi dan arsitektur

| Folder | Tujuan | Mesin pengambilan | Titik masuk |
|---|---|---|---|
| `gmaps-reviews-scraper` | Ulasan satu tempat dan metadata lokasi | Scrapling HTTP GetLocalBoqProxy; endpoint melalui browser; fallback DOM | `scraper.py:1658`, `scrape_reviews` |
| `instagram-scraper` | Komentar/balasan satu post; konten profil, hashtag, keyword | StealthySession, embedded JSON, respons GraphQL, scroll/click | `scraper.py:1583`, `scrape_comments`; `:1943`, `scrape_content` |
| `news-scraper` | Artikel dari 10 portal menurut query dan tanggal | Search, tag, RSS; browser dan HTTP | `scraper/engine.py:19`, `run_scrape` |
| `playstore-reviews-scraper` | Ulasan aplikasi berdasarkan package ID dan locale | `google_play_scraper.app/reviews`, continuation token | `scraper.py:330`, `scrape_reviews` |
| `saham-scraper` | OHLCV, adjusted close, dividen, split | Yahoo chart melalui curl_cffi, retry dua host | `scraper.py:510`, `scrape_prices` |
| `threads-scraper` | Post berdasarkan keyword dan rentang tanggal | Embedded JSON dan GraphQL melalui browser | `scraper.py:621`, `scrape_posts` |
| `x-tweet-scraper` | Search, profile, tweet/reply, search/reply | Perekaman GraphQL SearchTimeline/UserTweets/TweetDetail | `scraper.py:487`, `scrape_tweets` |
| `youtube-comments-scraper` | Komentar utama dan balasan video | Innertube continuation; fallback DOM browser | `scraper.py:1814`, `scrape_comments` |

Semua memakai `app.py` sebagai UI Streamlit. Tujuh proyek memakai satu
`scraper.py` sebagai backend. News sudah memisahkan model (`core.py`), query
(`query.py`), transport (`fetchers.py`), konfigurasi/parser portal
(`portals.py`), dan orkestrasi (`engine.py`).

Alur umum: input → normalisasi/validasi → pengambilan → parsing → deduplikasi
dan filter → tabel/ringkasan → unduhan. News mengekspor CSV; tujuh proyek lain
menyediakan CSV dan JSON. Filter tabel pada proyek ulasan umumnya tidak
mengubah seluruh dataset yang diekspor.

## Pengelolaan proses dan penyimpanan

- Google Maps, Instagram, Play Store, saham, dan YouTube memakai `ScrapeJob`
  dengan `threading.Lock`, snapshot, dan fragment untuk pembaruan log.
- Instagram, Play Store, YouTube memiliki `stop_event`. Saham dan Google Maps
  tidak menyediakan mekanisme pembatalan job yang setara.
- X dan Threads memakai worker yang langsung membaca/menulis
  `st.session_state`, dengan konteks Streamlit yang ditempelkan ke thread.
- News menjalankan `run_scrape` secara sinkron dan memperbarui log via callback.
- Hasil utama disimpan dalam memori/session state; belum ada checkpoint hasil
  berkala atau resume job setelah aplikasi berhenti.
- Profil browser persisten ditujukan ke `sessions/google_profile`,
  `sessions/instagram_profile`, `sessions/threads_profile`, dan
  `sessions/x_profile`. Isi cookie tidak diperiksa dalam pembelajaran ini.
- Pada pemeriksaan ini hanya news dan X memiliki `.venv` di folder proyek.
  Hal ini tidak membuktikan dependensi proyek lain tidak tersedia di luar folder.
- Tidak ditemukan test suite aplikasi, konfigurasi CI, lockfile dependensi,
  atau direktori `.git` dalam pohon proyek yang diperiksa. Requirements memakai
  batas bawah versi (`>=`), sehingga instalasi ulang belum reproducible.

## Karakteristik penting tiap proyek

### Google Maps

Mode UI default adalah stealth; default fungsi backend adalah API. Link dengan
feature ID dapat langsung memakai endpoint; link pendek/nama lokasi membutuhkan
resolusi browser. Pencarian nama memilih hasil pertama. Mode API tidak mengambil
suka maupun balasan pemilik: nilai diekspor sebagai `0` dan string kosong.
Timestamp API dapat jatuh kembali ke perkiraan label relatif. Mode browser
mempunyai batas keras 30 menit dan 900 scroll. CSV memiliki sanitasi awalan
formula. Metadata tempat dapat minimal bila feature ID langsung tersedia.

### Instagram

Ada normalisasi URL post/profil/tag, pembacaan nama cookie lokal untuk indikator
sesi, dan pembersihan lock profil yang PID-nya sudah mati. Komentar dipetakan
ke ID induk/root; konten dapat memakai tanggal perkiraan dari media ID.
Slide carousel dilewati saat penelusuran JSON. Respons jaringan diproses memakai
set ID agar tidak bergantung urutan kedatangan. Batas jumlah konten diterapkan
saat pengumpulan sebelum filter tanggal. Exception browser mengembalikan hasil
kosong meskipun state internal mungkin telah memuat sebagian data.

### Berita

Kompas/Detik memakai search; CNN/CNBC/Republika/Suara/Tribun/JPNN/IDX memakai
halaman tag; Tempo memakai RSS. IDX hanya satu halaman tag karena `tag_page=None`.
RSS tidak menyediakan penelusuran arsip lengkap. Parser artikel memakai selector
portal lalu fallback `article p`/`main p`, dengan syarat minimal dua paragraf
yang masing-masing cukup panjang. Artikel tanpa tanggal atau isi tetap dapat
lolos bila judul memenuhi query. README masih menyebut nama folder/launcher lama.

### Play Store

Batch 200, retry tiga kali, continuation, deduplikasi reviewId, filter bintang
di endpoint, dan penyaringan komentar kosong. Hasil sebagian dipertahankan pada
gangguan umum. Lima batch tanpa baris baru menghentikan proses. Default UI
jumlah dan waktu adalah nol: tanpa batas yang ditetapkan pengguna. Data dan
log tetap dikumpulkan dalam memori. Identitas unik pada UI dihitung dari nama.

### Saham

Normalisasi kode IDX/AS/kripto/kustom; interval harian, mingguan, bulanan.
Harga dikonversi ke tanggal zona bursa, dengan fallback Jakarta. Data OHLCV
kosong dapat diisi dari close, volume nol, atau harga pasar terakhir. UI memakai
close untuk return/volatilitas/drawdown; bukan perhitungan total return.
Plotly menyediakan candle, MA20/50, volume, return bulanan, dan drawdown.

### Threads dan X

Threads memakai HTML awal + GraphQL, deduplikasi ID, lalu filter tanggal WIB.
X menangkap respons GraphQL sesuai mode; retweet dinormalisasi menjadi tweet
asli. Keduanya memiliki jeda berkala dan batas 600 scroll. X mengukur stagnasi
dari jumlah kartu DOM, sedangkan Threads dari jumlah post terkumpul. Filter
tanggal pada profil/Threads bekerja pada data yang berhasil dimuat, sehingga
tidak membuktikan cakupan seluruh arsip.

### YouTube

Mode API membaca ytInitialData/player/config, mengambil komentar utama, lalu
balasan opsional. Format payload baru dan legacy memiliki parser terpisah.
Tanggal komentar merupakan perkiraan label relatif pada kedua mode. Browser
tidak mengambil balasan. Mode API dapat fallback ke browser. CSV memiliki
sanitasi awalan formula. UI langsung rerun setelah memulai worker, berbeda
dari beberapa salinan pola job di proyek lain.

## Temuan yang dapat ditindaklanjuti

### Terbukti melalui eksekusi lokal

1. **Parser query berita gagal pada kurung yang menempel pada kata.**
   `PLTS OR PLTB` berhasil, tetapi `(PLTS OR PLTB)` menghasilkan
   `ValueError: Tanda kurung tidak seimbang.` Token `\S+` ikut menyerap `)`.
   Lokasi: `news-scraper/scraper/query.py:17`.
2. **Timestamp Instagram berbentuk string angka dibuang.**
   `_to_datetime(1700000000)` menghasilkan `2023-11-15 05:13:20+07:00`;
   `_to_datetime('1700000000')` menghasilkan `None`. Hasil sama untuk bentuk
   milidetik. Cabang regex langsung `continue`, melewati konversi float.
   Lokasi: `instagram-scraper/scraper.py:335`. Komentar tanpa timestamp valid
   kemudian dibuang oleh `_comment_row`.
3. **ID induk komentar Instagram berbentuk integer hilang.**
   Input sintetis dengan `parent_comment_id=456` menghasilkan `parent_id=''`.
   `_first_str` hanya menerima string, sementara ID komentar sendiri sudah
   memakai `_id_str`. Lokasi: `instagram-scraper/scraper.py:1051`.
   Ini membuktikan perilaku parser pada tipe tersebut, bukan frekuensi tipe
   tersebut dalam respons Instagram saat ini.

### Terlihat dari implementasi; belum diuji end-to-end

4. **Berita dapat berhenti terlalu cepat.** `engine.py:139` menghentikan query
   bila satu halaman berisi artikel baru tetapi tidak ada yang cocok. Tidak
   ada jaminan halaman selanjutnya juga tidak cocok. `engine.py:67` juga
   berhenti pada daftar kosong, termasuk daftar tag yang kosong karena filter
   tanggal meskipun halaman berikutnya dapat masuk rentang.
5. **X/Threads dapat memulai job tumpang tindih.** Tombol mulai tidak
   dinonaktifkan ketika job aktif (`x-tweet-scraper/app.py:177`,
   `threads-scraper/app.py:163`). Semua worker berbagi field session state
   dan profil browser yang sama. Ini membuka kemungkinan hasil/log saling
   menimpa dan konflik profil; kejadian runtime belum direproduksi.
6. **YouTube belum menjaga integritas balasan dan deadline secara menyeluruh.**
   Loop balasan `scraper.py:1320` tidak memeriksa deadline pada setiap halaman
   dan append baris di `:1353` tidak memakai deduplikasi ID balasan. Batas
   per komentar dapat tercapai oleh baris berulang jika endpoint mengulangnya.
7. **Metadata ekspor YouTube dapat berubah setelah scraping.**
   `app.py:855` dan `:857` mengambil batas komentar/balasan dari widget saat
   ekspor, bukan snapshot konfigurasi hasil. Mengubah sidebar dapat membuat
   metadata tidak sesuai pengambilan yang menghasilkan dataset tersebut.
8. **Perlakuan pasar kripto di grafik saham belum dipisahkan.**
   `app.py:801` menyembunyikan akhir pekan untuk semua interval harian;
   `:217` menggunakan 252 periode/tahun untuk semua data harian. Parameter
   ini tidak mempertimbangkan pilihan pasar/instrumen.
9. **Missing data belum konsisten.** Google Maps mengisi suka API dengan nol;
   saham mengisi beberapa OHLCV kosong; Instagram baru mengubah metrik menjadi
   null jika seluruh kolom bernilai nol. Nol teramati dan data tidak tersedia
   belum dibedakan per baris secara konsisten.
10. **Klaim kelengkapan terlalu kuat di sebagian teks.** Batas scroll, stagnasi,
    waktu, discovery tag/RSS, filtering, dan kegagalan parser membatasi cakupan.
    Jumlah baris kecil tidak otomatis membuktikan pembatasan dari platform.

## Urutan pengembangan yang disarankan

1. Perbaiki parser berita dan Instagram, dengan fixture regression offline
   untuk query, timestamp, ID induk, dan tipe data opsional.
2. Standarkan hasil job: status lengkap/parsial/gagal, alasan berhenti,
   konfigurasi immutable, waktu pengambilan, sumber/mode, serta jumlah data
   mentah, valid, duplikat, dan ditolak. Pertahankan hasil parsial saat error.
3. Perbaiki pagination dan batas waktu, lalu samakan pengelolaan worker
   X/Threads dengan pola job terisolasi. Uji alur tombol mulai/stop/rerun.
4. Tambahkan checkpoint untuk proses panjang, versi dependensi terkunci,
   dan pengujian smoke per platform dengan target kecil yang ditentukan.
5. Setelah kontrak perilaku stabil, ekstrak utilitas bersama untuk job,
   ekspor, waktu, dan pelaporan. Jangan langsung menggabungkan seluruh scraper
   menjadi satu aplikasi besar; parser platform tetap perlu terpisah.

## Menjalankan proyek

Setiap folder menyediakan launcher `Jalankan ... .command` yang membuat `.venv`
jika executable Streamlit belum ada, memasang dependensi, lalu menjalankan UI.
Launcher news memasang paket langsung, bukan membaca requirements, sehingga
perlu diselaraskan jika daftar dependensi berubah.

Cara manual dari folder proyek setelah dependensi tersedia:

```bash
.venv/bin/streamlit run app.py
```

Jika menjalankan beberapa aplikasi bersamaan, tentukan port berbeda, misalnya
`--server.port 8502`. Browser runtime diperlukan untuk jalur Scrapling browser;
Play Store dan saham tidak memerlukan browser. Status instalasi/runtime tiap
proyek belum diuji dalam pemeriksaan ini.

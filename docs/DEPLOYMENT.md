# Deployment Scraper Workspace

## Server Linux milik sendiri

Panduan ini untuk satu operator. Jalankan perintah berikut dari root repositori
dengan Python 3.11 dan akun layanan biasa. Pemasangan library sistem Chromium
dapat memerlukan izin administrator server.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m patchright install --with-deps chromium
mkdir -p "$HOME/scraper-workspace-data"
SCRAPER_WORKSPACE_DATA="$HOME/scraper-workspace-data" \
  .venv/bin/python -m streamlit run workspace_app.py \
  --server.address 127.0.0.1 --server.port 8510 --server.headless true
```

Simpan `SCRAPER_WORKSPACE_DATA` di disk persisten di luar clone repositori.
Jalankan hanya satu proses Workspace untuk direktori data yang sama.

Dari laptop, buka akses lewat SSH:

```bash
ssh -L 8510:127.0.0.1:8510 user@server
```

Lalu buka [localhost:8510](http://127.0.0.1:8510). Jika memakai reverse proxy,
atur HTTPS dan autentikasi di proxy sebelum memberi akses. Login platform manual
membuka browser di mesin server dan memerlukan sesi desktop yang dapat digunakan
operator. Mode browser tanpa jendela tidak memberi alur login manual dari
halaman Streamlit.

## Streamlit Community Cloud

Pada saat deploy, gunakan repositori GitHub ini, branch `main`, entrypoint
`workspace_app.py`, dan pilih Python **3.11** di **Advanced settings**.

Sebelum deploy pertama, siapkan dan uji langkah pemasangan Chromium Patchright
serta library Linux pada lingkungan host. Community Cloud memasang paket Python
dari `requirements.txt`, tetapi launcher laptop bukan bagian dari startup Cloud.
Konfigurasi lokal di `.streamlit/config.toml` mengunci bind ke `127.0.0.1:8510`.
Konfigurasi tersebut perlu disesuaikan untuk host agar alamat dan port mengikuti
runtime penayangan aplikasi.

Untuk uji terbatas, gunakan aplikasi private dan beri akses hanya kepada
operator. Riwayat SQLite, log, status pekerjaan, dan profil browser dibagi oleh
satu proses aplikasi. Akses private membatasi siapa yang masuk, tetapi tidak
memisahkan state di antara viewer. Siapkan penyimpanan persisten dan pemisahan
pengguna sebelum mengandalkan aplikasi untuk riwayat jangka panjang atau akses
bersama. Login platform yang membutuhkan browser tampak juga belum memiliki
alur pada Community Cloud.

Rujukan resmi:

- [Deploy your app on Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [App dependencies](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)
- [Share your app dan pengaturan private](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app)

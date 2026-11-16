# BB WATCH — Bollinger Bands Squeeze Monitor (Vercel)

**Dashboard Python/FastAPI + Telegram + Upstash Redis + Binance public spot market data.**

Ini versi dengan UI. Kamu bisa **tambah, hapus, jeda, atau aktifkan instrumen** langsung dari browser, tanpa mengedit kode atau redeploy. Market yang didukung adalah **pasangan crypto Binance Spot** (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`, dll.); timeframe **1 jam** tetap untuk V1. Semua instrumen baru divalidasi ke API public Binance.

> Read-only: **tidak ada integrasi trading, order, exchange key, deposit, atau leverage**. Notifikasi hanyalah observasi volatilitas, bukan saran membeli/menjual.

## 1. Arsitektur

```text
Browser: dashboard (login + tambah/hapus/jeda simbol)
     |
     v
FastAPI Vercel function (app.py)
     |------> Upstash Redis REST: daftar simbol, status, lock, anti-duplikasi
     |------> Binance public API: exchangeInfo / klines (1h, closed candles)
     `------> Telegram Bot API: kirim alert ketika squeeze baru mulai

Cron (setiap jam, menit :13 UTC)
  - Hobby: GitHub Actions -> GET /api/monitor (Bearer CRON_SECRET)
  - Pro: Vercel Cron -> GET /api/monitor (Bearer CRON_SECRET)
```

**Catatan penting:** Vercel Hobby Cron bawaan dibatasi sekali per hari, jadi paket ini menyediakan **GitHub Actions** untuk jadwal pemantauan setiap jam. GitHub Actions `schedule` bersifat best-effort: job bisa terlambat atau terlewat. Dengan Vercel Pro, gunakan `vercel.pro.json.example` sebagai `vercel.json` untuk cron bawaan.

## 2. Metode deteksi

- Bollinger Bands: SMA 20, 2 × populasi standar deviasi.
- `BandWidth% = (Upper-Lower)/abs(Middle) × 100`.
- Cutoff: persentil 20 dari **120 BandWidth sebelumnya**, tanpa memakai data masa depan.
- Squeeze ketika BandWidth sekarang kurang dari cutoff (atau width nol); tie positif tidak dihitung sebagai squeeze.
- Alert **sekali** ketika candle sebelumnya **tidak squeeze** dan candle sekarang **squeeze**.
- Hanya memakai **closed candle** 1 jam; 30 detik grace; data lama >150 menit ditolak untuk notifikasi.
- Redis `SET NX EX` mengurangi duplikasi saat cron dipanggil berulang. Tidak menjamin exactly-once pada timeout Telegram; hanya best effort.
- Deteksi squeeze **tidak memprediksi arah** breakout.

## 3. Siapkan Telegram

1. Buat bot lewat `@BotFather` di Telegram.
2. Tambahkan bot ke grup yang dituju dan pastikan boleh mengirim pesan.
3. Ambil **Chat ID grup**. Nilai negatif, misalnya `-100...`, adalah format wajar.
4. **Ganti token bot jika pernah dibagikan atau terunggah ke GitHub.** Jangan menaruh token di frontend atau meng-commit `.env`.

## 4. Buat Upstash Redis

1. Buka [Upstash Console](https://console.upstash.com/redis).
2. Buat Redis database baru, pilih region dekat Vercel bila tersedia.
3. Copy **REST URL** dan **REST Token** (Standard / write-enabled).
4. Jangan gunakan Read-Only Token: dashboard membutuhkan operasi `SADD`, `SREM`, `SET`.

Penyimpanan persisten penting: filesystem Vercel Function bukan database dan tidak menyimpan perubahan permanen antar invocation.

## 5. Environment Variables

Copy `.env.example` ke `.env` untuk lokal, kemudian isi:

| Key | Tujuan |
|---|---|
| `ADMIN_PASSWORD` | Password masuk dashboard (minimal 12 karakter; dianjurkan 20+ acak) |
| `SESSION_SECRET` | Kunci cookie bertanda tangan (32+ karakter acak, berbeda dari password) |
| `CRON_SECRET` | Token untuk melindungi endpoint cron (24+ karakter acak) |
| `UPSTASH_REDIS_REST_URL` | Endpoint REST database Upstash |
| `UPSTASH_REDIS_REST_TOKEN` | Standard token Upstash untuk operasi tulis |
| `TELEGRAM_BOT_TOKEN` | Token Telegram yang **baru/aman** |
| `TELEGRAM_CHAT_ID` | ID grup tujuan notifikasi (boleh negatif) |
| `APP_ENV` | `local` hanya untuk HTTP saat development; **jangan** isi `local` pada Vercel |

Buat secret acak dengan Python:

```bash
python -c "import secrets; print(secrets.token_urlsafe(40))"
```

Jalankan perintah itu **tiga kali** untuk password, session secret, dan cron secret; jangan pakai nilai yang sama.

Di Vercel: **Project → Settings → Environment Variables**; set semua key kecuali `APP_ENV`. Jangan gunakan tanda kutip ekstra di form Vercel.

## 6. Jalankan lokal

```bash
python3 -m venv .venv
source .venv/bin/activate        # Linux / macOS
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env dan isi secret, Redis, Telegram
python run_local.py
```

Buka http://127.0.0.1:8000 dan masuk memakai `ADMIN_PASSWORD`.

Setelah itu:

1. Masukkan `BTCUSDT` atau tekan chip tambah BTC/USDT.
2. Tekan **Scan sekarang** untuk mengecek candle terakhir.
3. Perhatikan status per instrumen. Squeeze aktif tidak selalu mengirim notifikasi: hanya **awal transisi** yang memicu alert.
4. Tekan **Tes Telegram** untuk menguji pesan grup.
5. Tekan **Jeda**, **Aktifkan**, atau **Hapus** untuk mengubah daftar monitoring.

## 7. Deploy ke Vercel

1. Buat repository GitHub baru lalu unggah **isi folder proyek** ini ke root repository. Jangan unggah `.env`.
2. Buka [Vercel New Project](https://vercel.com/new), pilih repository dan framework FastAPI jika diminta.
3. Biarkan Root Directory = root repo (lokasi `app.py`).
4. Set Environment Variables dari langkah 5 untuk **Production** (dan Preview jika perlu).
5. Deploy.
6. Buka URL Vercel, login, tambahkan instrumen, lalu tes Telegram dan scan manual.

`vercel.json` bawaan **tidak mengaktifkan Cron Vercel**, supaya deployment Hobby tidak ditolak karena jadwal per jam. Proyek memakai Vercel Python FastAPI entrypoint `app.py` dan satu serverless function.

## 8. Jadwalkan scan 1H pada Vercel Hobby (GitHub Actions)

File `.github/workflows/hourly-monitor.yml` sudah disertakan. Di repository GitHub:

**Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `MONITOR_URL` | URL utama, contoh `https://your-project.vercel.app` (tanpa `/api/monitor`) |
| `CRON_SECRET` | Sama persis seperti Environment Variable `CRON_SECRET` pada Vercel |

Workflow berjalan pada menit 13 setiap jam UTC (contoh WIB: 07:13, 08:13, 09:13) dan tersedia tombol manual **Run workflow** di GitHub Actions. Waktu bisa terlambat/terlewat akibat antrean GitHub. Cron scan hanya membaca data dan mengirim alert; tidak mengambil order.

## 9. Jadwalkan scan pada Vercel Pro (opsional)

Salin `vercel.pro.json.example` menjadi `vercel.json`, commit, redeploy. Contoh cron:

```json
{"crons":[{"path":"/api/monitor","schedule":"13 * * * *"}]}
```

Karena `CRON_SECRET` diset pada Vercel, Vercel Cron otomatis memanggil endpoint dengan `Authorization: Bearer <CRON_SECRET>`.

**Pilih hanya satu scheduler** untuk menghindari pemindaian duplikat (meskipun lock telah diterapkan).

## 10. Endpoint

| Endpoint | Method | Akses |
|---|---|---|
| `/` | GET | Public: halaman login, tidak menampilkan data/secret |
| `/api/auth/login` | POST | Password admin |
| `/api/auth/logout` | POST | Logout cookie |
| `/api/auth/me` | GET | Cek sesi |
| `/api/instruments` | GET | Cookie login |
| `/api/instruments` | POST | Cookie login; validasi Binance Spot |
| `/api/instruments/{SYMBOL}` | DELETE | Cookie login |
| `/api/instruments/{SYMBOL}/pause` | PATCH | Cookie login |
| `/api/scan` | POST | Cookie login; scan manual + kemungkinan alert |
| `/api/telegram/test` | POST | Cookie login; tes pesan |
| `/api/monitor` | GET | Authorization Bearer CRON_SECRET |
| `/api/health` | GET | Public; status minimal |

`/api/monitor` **tidak** boleh dipanggil dengan URL berisi secret query string. Gunakan header Authorization.

## 11. Keamanan dan operasional

- UI hanya dibuka setelah login; cookie HttpOnly, SameSite=Strict dan Secure (untuk HTTPS Vercel).
- Secret hanya di server, tidak masuk HTML/JS. Jangan pernah membagikan `ADMIN_PASSWORD`, token Telegram, `CRON_SECRET`, atau Upstash Token.
- Batasi akses atau gunakan Vercel WAF/rate limit untuk endpoint login jika proyek dibuat publik.
- Batasi 20 instrumen untuk menjaga durasi serverless dan permintaan API. Ada batas/rate-limit exchange dan layanan gratis.
- Jika Binance API tidak dapat dijangkau dari region Vercel, scan akan menampilkan Error; tidak ada fallback ke data pasar palsu.
- GitHub Actions schedule dan Vercel cron tidak menjamin scan tanpa terlewat (bukan real-time market feed).
- Interval 1H masih tetap pada V1; mengganti timeframe per instrumen belum didukung.
- Antarmuka memformat waktu terakhir scan dalam WIB. Perhitungan dan scheduler bekerja dengan UTC.

## 12. Uji kode

```bash
python -m pytest -q
```

Test memeriksa logika hitung squeeze, input simbol, keamanan cookie, akses API, dan deduplikasi scanner menggunakan data sintetis/mocks. Tidak mengirim request nyata ke Binance/Telegram/Upstash.

## Referensi resmi

- [Vercel FastAPI](https://vercel.com/docs/frameworks/backend/fastapi)
- [Vercel Cron Jobs](https://vercel.com/docs/cron-jobs)
- [Upstash Redis REST API](https://upstash.com/docs/redis/features/restapi)
- [Binance Market Data Only](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md)
- [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage)
- [GitHub Actions schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

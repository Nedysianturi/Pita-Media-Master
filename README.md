# 🎬 Pita Media - Autonomous Multi-Agent Content Engine

**Pita Media** adalah sistem agen otonom tingkat lanjut yang dirancang untuk merencanakan, membuat, melakukan pemeriksaan kualitas (QC) mandiri, memperbaiki secara otomatis (*self-repair*), mempublikasikan, memverifikasi hasil postingan, dan mengoptimalkan strategi konten multi-pilar secara berkelanjutan tanpa perlu intervensi manual satu per satu.

Sistem ini ditenagai secara eksklusif oleh **Ekosistem Google Gemini** (Gemini 2.5, Google Veo, Imagen 3) dan **FFmpeg** untuk finishing media beresolusi tinggi.

---

## 🏛️ 4 Pilar Konten Wajib

1. **Pita Transformasi**:
   - Video transformasi / timelapse bertahap (9:16).
   - Struktur 6 fase progresif yang ketat: *Kondisi Awal -> Proses -> Perubahan Bertahap -> Detail Makro -> Finishing -> Final Reveal*.
   - Konsistensi visual, pencahayaan, dan stabilitas sudut pandang dijaga ketat.
2. **Pita Mini**:
   - Video konstruksi / transformasi miniatur orisinal dan memuaskan (*oddly satisfying*).
   - Struktur material mikro (balsa, resin, semen mikro) menuju *final reveal* detail berbobot.
3. **Pita Cerita**:
   - Postingan **3 sampai 5 gambar statis dalam carousel** (format 1:1, murni gambar statis, bukan video/slideshow).
   - Caption berisi **cerita lengkap 150 sampai 300 kata** yang mendalam, puitis, dan menyentuh di bawah postingan.
4. **Pita Kreasi**:
   - Mengubah bahan sederhana/mentah menjadi karya menakjubkan dan tak terduga.
   - **Tanpa klaim palsu**: Menjunjung tinggi konteks seni konsep kreatif tanpa klaim fisis dunia nyata yang menyesatkan.
   - Dilengkapi sentuhan tanda tangan halus (*signature mark*) **Pita Waktu** bila relevan.

---

## 🏗️ Arsitektur & Peran Agen

- **Creator Agent**: Riset ide, prompt engineering berbasis pilar, pembuatan video via Veo, gambar via Imagen, dan finishing FFmpeg.
- **Reviewer Agent**:
  - **Hard Safety Gate**: Memblokir tegas konten yang berisiko atau melanggar kebijakan (`BLOCKED`).
  - **Multi-Criteria Rubric**: Penilaian skor QC per kategori (min 7.0) dan skor total (min 8.0).
  - **Self-Repair Engine**: Jika berstatus `REPAIR_REQUIRED`, sistem otomatis memperbaiki prompt/narasi, menyimpan riwayat *diff* sebelum-sesudah, dan menguji ulang hingga batas maksimal (3x).
- **Publisher Agent & Verifier**:
  - Memastikan konten berstatus `PASSED` sebelum diposting.
  - Menghitung hash sidik jari konten dan memverifikasi histori 30 hari untuk mencegah duplikasi (*double post*).
- **Strategist & Governors**:
  - **Cost Governor**: Menjaga batas pengeluaran harian dan bulanan dengan proteksi *hard limit*.
  - **Frequency Governor**: Mengatur jeda minimum antar posting.
  - **Novelty & Fatigue Engine**: Mencegah kejenuhan topik dengan rolling window 7-30 hari dan mengalokasikan **20-30% kuota untuk eksplorasi eksperimental**.
  - **Anomaly Detector**: Prinsip *Anti-Knee-Jerk* (tidak mengubah strategi besar hanya karena satu konten fluktuatif).

---

## 🔐 Panduan Konfigurasi Kredensial (Mandiri & Aman)

> [!IMPORTANT]
> **JANGAN PERNAH** membagikan API key di chat atau meng-commit file `.env` ke Git repository! File `.env` Anda sudah terdaftar di `.gitignore`.

### Langkah 1: Dapatkan Google Gemini API Key
1. Buka [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Login dengan akun Google Anda dan klik tombol **Create API Key**.
3. Salin API key yang dihasilkan.

### Langkah 2: Buat Bot Telegram & Dapatkan Admin ID
1. Buka Telegram dan cari [@BotFather](https://t.me/BotFather).
2. Ketik `/newbot`, ikuti petunjuk nama bot Anda, lalu salin **Bot Token** yang diberikan (contoh format: `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).
3. Cari [@userinfobot](https://t.me/userinfobot) di Telegram untuk melihat **User ID** angka Anda sendiri (contoh: `987654321`).

### Langkah 3: Siapkan File `.env`
1. Salin template `.env.example` menjadi `.env`:
   ```bash
   copy .env.example .env
   ```
2. Buka file `.env` menggunakan teks editor Anda (Notepad/VSCode), lalu isi nilai:
   ```env
   GEMINI_API_KEY=masukkan_gemini_api_key_anda_di_sini
   TELEGRAM_BOT_TOKEN=masukkan_token_botfather_di_sini
   TELEGRAM_ADMIN_IDS=masukkan_user_id_telegram_anda_di_sini
   TELEGRAM_ALERT_CHAT_ID=masukkan_user_id_telegram_anda_di_sini
   DASHBOARD_SECRET_KEY=buat_kata_kunci_rahasia_bebas_untuk_dashboard
   ```
3. Simpan file `.env`.

---

## 🚀 Panduan Menjalankan Sistem

Pastikan lingkungan virtual aktif:
```bash
# Windows PowerShell / CMD
.venv\Scripts\activate
```

### 1. Inisialisasi Database
```bash
python main.py init-db
```

### 2. Uji Coba 1 Siklus Konten Mandiri (Dry-Run / Live)
Untuk membuat 1 konten langsung pada pilar tertentu:
```bash
# Pilar Pita Cerita (3-5 gambar carousel + narasi 150-300 kata)
python main.py single --pilar pita_cerita

# Pilar Pita Transformasi (Video timelapse 9:16)
python main.py single --pilar pita_transformasi

# Pilar Pita Mini (Video miniatur diorama)
python main.py single --pilar pita_mini

# Pilar Pita Kreasi (Rekayasa bahan sederhana)
python main.py single --pilar pita_kreasi
```

### 3. Menjalankan Daemon Otomatis (Scheduler & Worker Engine)
```bash
python main.py run
```
Sistem akan otomatis:
- Menjalankan *Crash Recovery Scan* saat startup untuk memulihkan job yang terputus.
- Menjadwalkan konten berkala sesuai kalender dan alokasi eksplorasi 20-30%.
- Mengirimkan update real-time langsung ke Telegram Anda.

### 4. Menjalankan Status Dashboard Terproteksi
```bash
python main.py dashboard
```
Buka browser di: `http://127.0.0.1:8080?token=DASHBOARD_SECRET_KEY_ANDA`

### 5. Membuat Snapshot Backup Database
```bash
python main.py backup
```
File snapshot akan disimpan di folder `storage/backups/`.

---

## 📱 Perintah Remote Control Telegram (C2)

Dari mana pun Anda berada, cukup kirimkan pesan ke Bot Telegram Anda:
- `/status` : Menampilkan kesehatan sistem, worker aktif, rekapitulasi antrean, dan penggunaan biaya harian/bulanan.
- `/pause` : Menjeda pengambilan job baru dari antrean.
- `/resume` : Melanjutkan pemrosesan antrean konten.
- `/report` : Melihat rekapitulasi performa, rata-rata skor QC, views, dan ROI score.
- `/job <id>` : Melihat detail lengkap suatu job.
- `/emergency_stop` : Menghentikan seluruh proses seketika dan mengunci sistem.

---

## 🧠 Pita Media Learning Intelligence System

Pita Media dilengkapi dengan **Learning Intelligence Engine** yang memungkinkan sistem belajar secara empiris dari performa nyata tanpa pernah meningkatkan wewenang tanpa kontrol manusia (*Never increase authority without control*).

### 🏛️ 4 Level Otonomi Sistem (Autonomy Levels)

1. **LEVEL 1: `OBSERVE` (Default Awal Wajib)**:
   - Sistem **hanya mengamati, mengumpulkan data telemetri, mencatat memori jangka panjang, dan mendistilasi pola**.
   - Sistem dilarang mengubah strategi, jadwal, atau bobot pilar secara otomatis.
2. **LEVEL 2: `RECOMMEND`**:
   - Sistem menganalisis tren performa dan **menyajikan rekomendasi strategis** (perubahan alokasi pilar, formula hook, model AI terbaik) ke Admin.
   - Keputusan eksekusi tetap menunggu persetujuan Admin.
3. **LEVEL 3: `ASSISTED_AUTO`**:
   - Sistem diberi wewenang melakukan penyesuaian minor dalam batas toleransi aman terkonfigurasi (memilih jam posting terbaik, variasi hook, menjalankan A/B test terkontrol).
4. **LEVEL 4: `CONTROLLED_AUTO`**:
   - Sistem mengoptimalkan strategi mandiri dalam batas toleransi ketat.

> [!CAUTION]
> **PROTECTED SETTINGS (Dilarang Diubah Mandiri oleh Sistem)**:
> Learning Engine dilarang keras mengubah sendiri: API Keys/Tokens, Safety Gate thresholds, Hard Cost Limit, Windows Service Config, Database Security, Telegram Admin ID, status `APP_MODE` (DRY_RUN/PRODUCTION), dan source code inti.

---

### 📊 Learning Maturity Score (0–100)

Skor kematangan sistem dihitung berdasarkan 6 pilar empiris:
1. **Valid Posts Count** (0–20 poin)
2. **Telemetry Snapshot Coverage** (0–20 poin)
3. **Completed A/B Experiments** (0–15 poin)
4. **Data Quality & Anomaly Cleanliness** (0–15 poin)
5. **Prediction vs Reality Accuracy** (0–15 poin)
6. **System Stability & Low Error Rate** (0–15 poin)

**Tahapan Kematangan**:
- `0–20` : **INSUFFICIENT_DATA**
- `21–40` : **EARLY_LEARNING**
- `41–60` : **DEVELOPING**
- `61–80` : **MATURE**
- `81–100` : **HIGH_CONFIDENCE**

---

### 🛡️ Proteksi Keamanan & Rollback

- **Automatic Downgrade**: Jika error rate $\ge 25\%$, terdeteksi anomali tinggi, atau kegagalan QC berturut-turut, level otonomi otomatis diturunkan (misal: `CONTROLLED_AUTO` $\rightarrow$ `ASSISTED_AUTO` $\rightarrow$ `RECOMMEND`).
- **Strategy Versioning & Rollback**: Setiap perubahan strategi dicatat versinya (`Strategy v1`, `v2`, dst.) dan dapat di-rollback seketika ke *last-known-good strategy* melalui Dashboard maupun API.
- **Data Quality Gate**: Menyaring metrik kotor, duplikasi, fake data simulasi dry-run, dan anomali bot spam sebelum masuk ke memori sistem.
- **Smart Failure Classifier**: Memisahkan kendala teknis (API/jaringan/timeout) dari kualitas ide konten agar ide bagus tidak tereliminasi karena masalah koneksi.

---

### 📱 Perintah Telegram C2 Learning

- `/learning` : Rangkuman status otonomi, skor kematangan, dan status belajar.
- `/maturity` : Rincian 6 pilar Learning Maturity Score (0–100).
- `/strategy` : Strategi aktif dan persentase alokasi pilar saat ini.
- `/lessons` : Daftar pola unggul yang terdistilasi dalam Knowledge Base.
- `/recommendations` : Rekomendasi kenaikan level otonomi teranalisis.

---

## 🔐 Persistent Credential Vault & Security Hardening

Sistem **Pita Media** mengimplementasikan arsitektur keamanan tingkat enterprise untuk memastikan seluruh token, API key, dan kredensial media sosial tersimpan secara persisten, aman, dan tahan banting terhadap *crash*, restart sistem operasi, maupun pembaharuan kode.

### 🛡️ Fitur Utama Keamanan Vault:

1. **Dual-Layer Encryption at Rest**:
   - Enkripsi native menggunakan **Windows DPAPI** (`CryptProtectData`/`CryptUnprotectData`).
   - Fallback authenticated cipher **AES-256-GCM** dengan derivasi kunci berbasis **PBKDF2-HMAC-SHA256** (100.000 iterasi).
   - Tersimpan di `storage/secure/credentials.vault` dan otomatis terabaikan oleh Git (`.gitignore`).

2. **Zero Plaintext Secret Leakage**:
   - **Centralized Redaction Filter**: Semua output terminal, file log (`logs/*.log`), dan pesan audit otomatis menyaring pola API key (Google `AIzaSy...`, Meta `EAA...`, xAI `xai-...`, Telegram `bot...`).
   - **Masked Fingerprints**: Nilai token hanya ditampilkan dalam format sidik jari aman (contoh: `••••••••7XQ2`) dan tidak pernah dikembalikan dalam format mentah ke browser.
   - **No Reveal Button**: Tidak ada tombol untuk mengekspos token mentah di Web Command Center demi kepatuhan ISO 27001 / SOC 2.
   - **Database Isolation**: Kolom SQLite `jobs`, `publications`, dan `audit_logs` dilarang menyimpan API key mentah.

3. **Multi-Generational Backup & Safe Mode**:
   - Setiap operasi penyimpanan secara atomik membuat rotasi backup `.bak1` dan `.bak2` dengan `fsync`.
   - Jika berkas utama `credentials.vault` terdeteksi rusak, sistem secara otomatis memulihkan dari `.bak1` atau `.bak2`.
   - Jika semua berkas rusak, sistem masuk ke **Safe Mode** untuk mencegah *crash*, mengisolasi operasi, dan mengirim peringatan darurat ke Telegram Admin.

4. **Atomic Secret Replacement**:
   - Siklus penggantian kredensial: `PENDING` $\rightarrow$ `PREFLIGHT TEST` $\rightarrow$ `ACTIVE`.
   - Jika uji koneksi gagal, sistem otomatis membatalkan penggantian dan mempertahankan kunci lama yang terbukti bekerja (`PREVIOUS`).

5. **Encrypted Disaster Recovery (`.pmvault`)**:
   - Ekspor dan impor brankas kredensial terenkripsi AES-256-GCM dengan passphrase mandiri.
   - Peringatan keamanan: *"Password backup tidak dapat dipulihkan oleh sistem Pita Media jika hilang."*

6. **12 Status Pemantauan Kesehatan Kredensial**:
   - `NOT_CONFIGURED`, `VALID`, `INVALID`, `EXPIRED`, `EXPIRING_SOON`, `MISSING_PERMISSION`, `RATE_LIMITED`, `QUOTA_EXHAUSTED`, `DISABLED`, `NEEDS_ATTENTION`, `UNKNOWN`, `VALID_EXPIRY_UNKNOWN`.
   - Status `RATE_LIMITED` (HTTP 429) secara ketat dipisahkan dari `INVALID` agar kendala batas kuota sementara tidak memicu penghapusan atau penonaktifan kredensial yang valid.


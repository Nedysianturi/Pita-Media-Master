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

## 🧪 Menjalankan Rangkaian Pengujian (Test Suite)

Untuk menjalankan seluruh 20+ unit & integration tests:
```bash
.venv\Scripts\pytest tests/ -v
```

Untuk memeriksa keamanan token sebelum commit:
```bash
python tools/secret_scanner.py
```

---

## 📦 Push ke GitHub Private Repository

1. Jalankan pemindaian rahasia untuk memastikan aman:
   ```bash
   python tools/secret_scanner.py
   ```
2. Inisialisasi git dan commit:
   ```bash
   git init
   git add .
   git commit -m "feat: inisialisasi sistem agen otonom Pita Media lengkap dengan 4 pilar, QC self-repair, dan Telegram C2"
   ```
3. Hubungkan ke repository GitHub privat Anda:
   ```bash
   git branch -M main
   git remote add origin https://github.com/USERNAME/REPO_NAME.git
   git push -u origin main
   ```

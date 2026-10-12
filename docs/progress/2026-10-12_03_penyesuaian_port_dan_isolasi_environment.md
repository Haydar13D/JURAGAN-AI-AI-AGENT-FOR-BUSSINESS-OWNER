# Catatan Progres: Penyesuaian Port & Isolasi Environment dari Konflik Docker Lama
## Tanggal: 2026-10-12 | Sesi: #03 | Fase: Setup & Environment Isolation

---

### 1. Tujuan Sesi / Iterasi
- Mengatasi kebingungan terkait akses port `8000` yang bertabrakan dengan kontainer Docker project lama (`sora-landing-chatbot`).
- Memastikan sistem JuraganAI 100% independen dan menggunakan port terisolasi yang bersih (`8005`).

### 2. Investigasi & Fakta Sistem
- **Temuan**: Pada Docker sistem lokal, kontainer `sora-landing-chatbot` aktif mengikat port host `0.0.0.0:8000->8000/tcp`.
- **Dampak**: Saat pengguna membuka `http://localhost:8000`, browser otomatis terhubung ke kontainer Docker lama (Sora Chatbot), BUKAN ke JuraganAI.
- **Konfirmasi**: JuraganAI sama sekali tidak menggunakan kode, model, atau API dari proyek Sora Seventh. JuraganAI murni dibangun dari awal di dalam folder `f:/AI-AGENT/` dengan arsitektur F&B POS Analytics & DeepSeek Agent.

### 3. Perubahan & Resolusi yang Diterapkan
- [x] Mengubah port default JuraganAI dari `8000` menjadi **`8005`** pada:
  - `f:/AI-AGENT/.env` (`PORT=8005`)
  - `f:/AI-AGENT/.env.example` (`PORT=8005`)
  - `f:/AI-AGENT/app/core/config.py` (`PORT: int = 8005`)
  - `f:/AI-AGENT/docker-compose.yml` (`8005:8005`)
  - `f:/AI-AGENT/README.md` (Update URL Swagger UI ke `http://localhost:8005/docs`)
- [x] Menyediakan launcher cepat `run_dev.py` agar pengguna cukup menjalankan:
  ```bash
  python run_dev.py
  ```
  untuk langsung menjalankan JuraganAI di port `8005` tanpa konflik.

### 4. Hasil Verifikasi
- Port `8005` telah diverifikasi berstatus bebas (*free/available*) di sistem Windows lokal.
- Seluruh pengujian PoC dan Pytest suite tetap berjalan 100% hijau (*5/5 passed*).
- JuraganAI kini dapat diakses secara independen di `http://localhost:8005` dan `http://localhost:8005/docs`.

# PRODUCT REQUIREMENTS DOCUMENT (PRD) v2.0

## JURAGANAI — Full Autonomous Dispatch & Mobile 1-Click Approval
**Inisiatif:** Zero-Friction Mobile-First Human-in-the-Loop Workflow  
**Target Rilis:** MVP v2.0 (Telegram-First Mobile Approval)  
**Tanggal:** 20 Oktober 2026  
**Status:** Ready for Review & Discussion  

---

## 1. Latar Belakang & Masalah (Problem Statement)

Pada implementasi PRD v1.0, JuraganAI telah berhasil menjalankan:
- Deteksi peluang deterministik (*Opportunity Detector*).
- Pembuatan draf strategi dan copywriting otomatis via LLM (*Campaign Planner*).
- Pembuatan materi visual promosi (*Flyer Generator*).
- Alur persetujuan (*Approval Workflow*) dan sandbox penerbitan (*Instagram Publisher*).

### Masalah Utama yang Tersisa:
Owner coffee shop di dunia nyata **tidak standby di depan laptop atau login ke web dashboard setiap hari**. Ketika agent mendeteksi penurunan penjualan, proposal hanya mengendap di database dalam status `WAITING_APPROVAL` sampai owner sengaja membuka web.

Akibatnya:
1. Kecepatan eksekusi promosi melambat (*loss of momentum*).
2. AI terasa masih membutuhkan "inisiatif manual user" untuk membuka web.
3. Potensi omzet yang hilang tidak segera dipulihkan pada hari yang sama.

---

## 2. Visi Produk & Solusi (The "Magic Moment")

> **Visi:** Mengubah JuraganAI menjadi partner bisnis mandiri yang datang proaktif ke smartphone owner pada waktu yang tepat, membawa materi kerjaan yang sudah matang, dan hanya meminta persetujuan dalam 1 kali ketukan jari (**1-Click Decision**).

### Alur Pengalaman Pengguna (Mobile Happy Path):
1. **08:00 WIB (Pagi Hari):** Scheduler background otomatis memindai data POS semalam.
2. **08:01 WIB:** Agent mendeteksi anomali penjualan (*Caramel Macchiato drop 82.5%*), menyusun strategi promosi, copywriting, dan merender flyer grafis.
3. **08:02 WIB (Proactive Push):** HP owner berdering menerima pesan Telegram:
   - Gambar Flyer promosi terlampir.
   - Pesan ringkas: Fakta penurunan, potensi omzet hilang, draf caption Instagram, dan batas diskon aman.
   - **Tiga Tombol Interaktif (Inline Keyboard):**
     - `[ ✅ Setujui & Publish ke IG ]`
     - `[ ✏️ Minta Revisi ]`
     - `[ ❌ Tolak / Abaikan ]`
4. **Keputusan Cepat:** Owner mengetuk **`[ ✅ Setujui ]`** langsung dari chat Telegram.
5. **08:03 WIB:** Bot membalas instan: *"Siap Juragan! Konten promosi resmi diterbitkan ke Instagram (@kopi_kenangan_senja). Siklus monitoring penjualan 7 hari ke depan telah dimulai! 🚀"*.
6. **Hari ke-7 (Closed-Loop Report):** Bot otomatis mengirim laporan evaluasi performa penjualan pasca-promosi ke chat Telegram owner.

---

## 3. Sasaran & Indikator Keberhasilan (Success Metrics)

| Indikator | Target v1.0 (Web) | Target v2.0 (Autonomous Mobile) |
| :--- | :--- | :--- |
| **Pemicu Alur Kerja** | Manual click di Web | 100% Terjadwal Otomatis (Scheduler Daemon) |
| **Waktu Pengambilan Keputusan** | > 4 jam (nunggu buka web) | < 30 detik via Telegram Inline Buttons |
| **Friction / Hambatan Login** | Perlu buka URL & login browser | Nol (Langsung di chat Telegram) |
| **Audit Trail & Idempotency** | Tersimpan di Web DB | Tersimpan di Web DB + Chat Transcript |
| **Closed-Loop Follow-up** | Manual review | Auto-dispatch laporan evaluasi hari ke-7 |

---

## 4. Functional Requirements (FR)

| ID | Kebutuhan Fungsional | Prioritas | Kriteria Penerimaan (Acceptance Criteria) |
| :--- | :--- | :---: | :--- |
| **FR-AUTO-01** | **Morning Autonomous Scheduler** | P0 | Background job berjalan setiap pukul 08:00 WIB (dapat dikonfigurasi). Mengeksekusi Opportunity Detector secara mandiri tanpa intervensi manusia. |
| **FR-AUTO-02** | **Telegram Photo & Rich Media Dispatcher** | P0 | Mengirim pesan promosi ke Telegram Owner berisi: (1) Foto flyer visual, (2) Bukti angka deterministik, (3) Strategi & caption, (4) Inline keyboard buttons. |
| **FR-AUTO-03** | **Interactive 1-Click Approval (Callback Query)** | P0 | Owner dapat mengetuk tombol `[Setujui]`, `[Revisi]`, atau `[Tolak]`. Sistem menangani event Telegram callback tanpa perlu membuka browser. |
| **FR-AUTO-04** | **Idempotent Button Locking** | P0 | Tombol Telegram dinonaktifkan/diedit setelah diklik agar tidak dapat ditekan berulang kali (mencegah double-posting). |
| **FR-AUTO-05** | **Smart Peak-Hour Auto-Publishing** | P1 | Opsi publikasi instan atau otomatis dijadwalkan pada jam sibuk optimal F&B (misal 13:45 WIB sebelum peak ngantuk sore). |
| **FR-AUTO-06** | **Day-7 Closed-Loop Evaluation Dispatcher** | P1 | Pada hari ke-7 setelah status `PUBLISHED`, sistem menghitung metrik delta penjualan dan mengirim rekap evaluasi secara otomatis ke Telegram. |

---

## 5. Spesifikasi Pesan Telegram & Interaksi Tombol

### Template Pesan Notifikasi Pagi (Proposal Dispatch):
```text
🚨 PELUANG PROMOSI TERDETEKSI (JuraganAI)
Outlet: Kopi Kenangan Senja

Halo Juragan! ☕ Hasil analisis transaksi semalam:
• Menu: Caramel Macchiato
• Sinyal: SHARP_DROP (Penjualan anjlok 82.5% minggu ini)
• Estimasi Omzet Hilang: Rp 6.048.000
• Margin Produk: 62.5% (Aman untuk promo)

💡 Rekomendasi Strategi:
"Afternoon Coffee Treat" — Diskon 15% (Rp 27.200) khusus jam santai 14.00–17.00 WIB untuk mengembalikan volume penjualan.

📝 Draf Caption Instagram:
"✨ Butuh mood booster sore ini? Nikmati manis legitnya Caramel Macchiato di Kopi Kenangan Senja! Promo spesial jam ngantuk diskon 15%..."

(Flyer materi promosi terlampir di atas)
```
### Inline Keyboard:
```
[ ✅ SETUJUI & PUBLISH KE INSTAGRAM ]
[ ✏️ MINTA REVISI ]    [ ❌ ABAIKAN / TOLAK ]
```

---

## 6. Aturan Bisnis & Guardrails Ketat
1. **No Auto-Posting Without Explicit Owner Tap:** Meskipun proposal dikirim otomatis, konten **TIDAK AKAN PERNAH** terbit ke Instagram tanpa ketukan tombol persetujuan dari owner.
2. **Cooldown Guardrail:** Maksimal 1 proposal promosi per hari dan produk yang sama dikenakan cooldown 7 hari agar owner tidak merasa di-spam.
3. **Graceful Delivery Failure:** Jika token Telegram tidak aktif atau chat ID belum terdaftar, proposal tetap tersimpan di Web Dashboard dengan status `WAITING_APPROVAL`.
4. **Idempotency Key:** Callback dari Telegram mengunci state kampanye ke `APPROVED` $\rightarrow$ `PUBLISHED` secara atomik di database.

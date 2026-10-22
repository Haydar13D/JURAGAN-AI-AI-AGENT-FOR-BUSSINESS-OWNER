# IMPLEMENTATION PLAN: Full Autonomous Dispatch & Mobile 1-Click Approval

Dokumen ini adalah rencana implementasi teknis untuk merealisasikan **PRD v2.0 (Zero-Friction Mobile-First Human-in-the-Loop Workflow)**.

---

## 1. Arsitektur Komponen & Alur Data

```
[APScheduler (Setiap Jam 08:00 WIB)]
       │
       ▼
[Autonomous Scanner Job] ────────► [OpportunityDetector.detect_opportunities]
       │
       ├─ (Jika tidak ada anomali) ──► Selesai (Idle)
       │
       ▼ (Jika ada anomali terdeteksi)
[CampaignPlannerService] ─────────► Buat Brief, Copywriting, & Flyer Asset
       │
       ▼
[Telegram Dispatcher Service] ────► Kirim Foto Flyer + Bukti + Inline Keyboard ke HP Owner
       │
       ▼
[Owner Smartphone (Telegram Chat)]
       │
       ├─ Ketuk [✅ SETUJUI] ──────► [TelegramBotManager Callback Handler]
       │                                  │
       │                                  ▼
       │                             [InstagramPublisherService.publish_campaign]
       │                                  │
       │                                  ▼
       │                             Update State ke PUBLISHED + Edit Pesan Telegram
       │                             (Tombol dikunci: "✅ Disetujui & Diterbitkan!")
       │
       ├─ Ketuk [✏️ REVISI] ───────► Minta input teks via reply Telegram -> Generate Versi Baru
       │
       └─ Ketuk [❌ TOLAK] ────────► Update State ke REJECTED di Database
```

---

## 2. File yang Terlibat & Rincian Perubahan

### A. Modifikasi `app/bot/telegram_bot.py`
1. **Dukungan Pengiriman Foto (sendPhoto API):**
   - Mengirim gambar flyer (`media_url`) bersama caption terformat markdown ke `chat_id`.
   - Menambahkan parameter `reply_markup` berisi `InlineKeyboardMarkup` dengan `callback_data` unik (contoh: `approve_camp_1`, `revise_camp_1`, `reject_camp_1`).
2. **Dukungan Callback Query Handling:**
   - Menangkap update Telegram dengan key `"callback_query"`.
   - Mengambil `data` dan `from.id`.
   - Menjalankan `InstagramPublisherService.publish_campaign`.
   - Memanggil `answerCallbackQuery` agar Telegram menampilkan notifikasi *"Sukses disetujui!"*.
   - Memanggil `editMessageReplyMarkup` untuk menghapus tombol agar tidak bisa diklik dua kali (*idempotency guardrail*).

### B. Modifikasi `app/services/daily_reporter.py`
1. **Penambahan Scheduled Job: `run_morning_autonomous_dispatch`**
   - Didaftarkan ke APScheduler pada jam `08:00 WIB` (konfigurasi via `.env`).
   - Memindai transaksi outlet aktif (`OpportunityDetector`).
   - Jika terdeteksi produk anjlok, otomatis memanggil `CampaignPlannerService` untuk merancang proposal.
   - Mengambil `telegram_chat_id` owner dari database (`Outlet.telegram_chat_id`).
   - Memanggil `TelegramBotManager.send_campaign_proposal(chat_id, campaign)`.

### C. Penambahan Endpoint Manual Trigger (Testing & Admin)
- Endpoint: `POST /api/v1/agent/dispatch-proposal-telegram?outlet_id=2`
- Memungkinkan admin atau developer memicu pengiriman proposal ke Telegram secara instan untuk kebutuhan demo / validasi tanpa harus menunggu jam 08:00 pagi.

---

## 3. Tahapan Pengerjaan (Step-by-Step Implementation)

### Tahap 1: Telegram Photo & Inline Keyboard Dispatcher (Sprint 1)
- Implementasi method `send_photo_with_buttons` di `TelegramBotManager`.
- Format tombol inline:
  ```json
  {
    "inline_keyboard": [
      [{"text": "✅ Setujui & Publish ke IG", "callback_data": "approve_camp_{id}"}],
      [
        {"text": "✏️ Minta Revisi", "callback_data": "revise_camp_{id}"},
        {"text": "❌ Tolak", "callback_data": "reject_camp_{id}"}
      ]
    ]
  }
  ```

### Tahap 2: Callback Query Processor & Lock Guardrail (Sprint 2)
- Menangani aksi klik:
  - `approve_camp_{id}`: memanggil `publish_campaign` dan memperbarui teks pesan Telegram menjadi:
    `"✅ PROPOSAL TELAH DISETUJUI & TAYANG DI INSTAGRAM (@kopi_kenangan_senja)"`.
  - `reject_camp_{id}`: memperbarui state campaign menjadi `REJECTED`.

### Tahap 3: Autonomous Morning Cron Scheduler (Sprint 3)
- Menggabungkan scheduler APScheduler di `app/services/daily_reporter.py`.
- Uji coba end-to-end tanpa prompt: Scheduler running $\rightarrow$ Opportunity terdeteksi $\rightarrow$ Pesan masuk ke HP $\rightarrow$ Owner klik Approve $\rightarrow$ Status terbit.

### Tahap 4: Unit Testing & Dokumentasi Final (Sprint 4)
- Menulis unit test untuk callback query handling & idempotency tombol Telegram.

# Log Progress: Implementasi PRD v1.0 Autonomous Business & Marketing Agent

**Tanggal:** 20 Oktober 2026  
**Status:** SELESAI & TERUJI (24/24 Tests Passed)  
**Tujuan:** Mengubah JuraganAI dari AI Assistant reaktif (chatbot Q&A) menjadi **Autonomous AI Marketing Agent** proaktif dengan alur kerja *closed-loop*, human-in-the-loop approval, dan multi-engine asset generation.

---

## 1. Latar Belakang & Validasi Kritik Owner

Sebelumnya, JuraganAI hanya bertindak sebagai chatbot analitik:
- Menunggu owner bertanya ("Kategori apa yang paling banyak omzet?").
- Hanya memberikan saran teks tanpa menghasilkan draf eksekusi.
- Tidak memiliki siklus pemantauan hasil kampanye (*closed-loop*).

Sesuai **PRD v1.0**, JuraganAI kini memiliki peran sebagai staff pemasaran mandiri:
1. **Proaktif**: Memindai transaksi POS secara otomatis tanpa perlu prompt user.
2. **Berbasis Bukti (Deterministik)**: Menghitung delta penjualan, omzet hilang, dan margin profit dengan matematika deterministik murni (bebas halusinasi LLM).
3. **Penyusun Draf Lengkap**: Menghasilkan target objektif, strategi brief, draf caption Instagram, dan materi visual flyer resolusi 1080x1080.
4. **Owner in Control (Human-in-the-Loop)**: Wajib approval owner sebelum konten terbit (`Approve`, `Revise`, `Reject`).
5. **Idempotent Publishing**: Mencegah publikasi ganda ke platform sosial media.
6. **Closed-Loop Evaluation**: Mengukur dampak penjualan pasca-promosi dengan guardrail korelasi observasional objektif.

---

## 2. Rincian Modul & Implementasi

### A. Dataset Transaksi Sintetis 60 Hari (FR-01)
- **File:** `scripts/generate_synthetic_pos.py` & `scripts/seed_synthetic_data.py`
- Menghasilkan 4.490 transaksi (8.064 baris) dengan *reproducible seed 42*.
- Mengandung skenario drop terukur: *Caramel Macchiato* anjlok 82.5% di 7 hari terakhir (dari 229 cup menjadi 40 cup), dengan potensi omzet hilang Rp6.048.000.
- Menetapkan margin sehat (*cost_price* Rp12.000, jual Rp32.000 = margin 62.5%).

### B. State Machine & Model Database (FR-09 & PRD Sec. 7)
- **File:** `app/models/campaign.py`
- Entitas baru:
  - `Opportunity`: Menyimpan sinyal deteksi (`SHARP_DROP`, `SLOW_MOVING_HIGH_MARGIN`), skor prioritas (0.0 - 1.0), dan bukti angka JSON.
  - `Campaign`: Menyimpan brief objektif, draf copywriting caption, URL visual flyer, persentase diskon aman, dan status state machine (`WAITING_APPROVAL`, `APPROVED`, `REVISION_REQUESTED`, `REJECTED`, `PUBLISHING`, `PUBLISHED`, `COMPLETED`).
  - `CampaignApproval`: Log audit persetujuan/revisi/penolakan dari owner.
  - `AgentEvent`: Audit timeline seluruh siklus event agent.
  - `SocialPost`: Log ID postingan dan status publikasi Instagram.

### C. Deterministic Opportunity Detector (FR-04)
- **File:** `app/services/opportunity_detector.py`
- Aturan deterministik:
  - Menghitung delta penjualan 7 hari terakhir (*Current Window*) vs 7 hari sebelumnya (*Baseline Window*).
  - Rule `SHARP_DROP`: Memicu jika menu aktif drop $\ge 30\%$ dan margin $\ge 30\%$.
  - Rule `SLOW_MOVING_HIGH_MARGIN`: Memicu jika margin $\ge 50\%$, stok tersedia, namun pergerakan lambat.
- **Guardrail Cooldown 7 Hari**: Mencegah usulan ganda untuk produk yang sama dalam kurun waktu 7 hari.

### D. Agent Planner via AtmoRouter (FR-06)
- **File:** `app/services/campaign_planner.py`
- Menggunakan provider AtmoRouter (*atmo/deepseek-v4.1-flash*) dengan fallback cascading ke DeepSeek Official & OpenRouter.
- Guardrail Margin & Diskon:
  - Margin $\ge 50\%$: Diskon maksimal 15-20%.
  - Margin 30-49%: Diskon maksimal 10% atau promo bundling.
  - Margin $< 30\%$: Dilarang memberikan diskon (hanya penonjolan rasa/kualitas).

### E. Multi-Engine Flyer Generator (FR-07)
- **File:** `app/services/flyer_generator.py`
- Mendukung opsi engine:
  - **SVG Template**: Desain dark-modern beresolusi 1080x1080 (Square Instagram Format), instan, 100% gratis tanpa biaya API.
  - **AI Image Generation**: Terintegrasi dengan Pollinations Flux (Free) dan OpenAI DALL-E 3 (Premium) dengan graceful fallback ke SVG jika terjadi kendala jaringan.

### F. Idempotent Instagram Publisher (FR-10)
- **File:** `app/services/instagram_publisher.py`
- Menerbitkan postingan ke sandbox Instagram mock dengan label simulasi.
- Menolak eksekusi ganda jika campaign sudah dalam status published.

### G. Closed-Loop Campaign Evaluator (FR-11)
- **File:** `app/services/campaign_evaluator.py`
- Membandingkan volume dan omzet sebelum vs sesudah masa tayang promosi.
- Mematuhi guardrail PRD: menyajikan korelasi observasional tanpa overclaiming kausalitas mutlak.

### H. Owner Approval Inbox Dashboard UI (FR-08)
- **File:** `app/templates/dashboard.html`
- Dilengkapi section khusus:
  - Kartu proposal interaktif dengan bukti data matematis.
  - Live preview flyer Instagram 1080x1080.
  - Draf caption dengan tombol copy 1-klik.
  - Tombol aksi: **`[✅ Setujui & Publish ke Instagram]`**, **`[✏️ Minta Revisi]`**, dan **`[❌ Tolak]`**.
  - Dropdown **Audit Timeline Log** (Agent Events).

---

## 3. Hasil Pengujian Unit Tests

```bash
python -m pytest tests/test_prd_v1.py -v
```
Output:
- `test_opportunity_detector_sharp_drop`: **PASSED**
- `test_flyer_generator_svg`: **PASSED**
- `test_instagram_publishing_and_idempotency`: **PASSED**
- `test_campaign_evaluator`: **PASSED**

*Total seluruh test suite proyek: **24/24 passed (100% success rate)**.*

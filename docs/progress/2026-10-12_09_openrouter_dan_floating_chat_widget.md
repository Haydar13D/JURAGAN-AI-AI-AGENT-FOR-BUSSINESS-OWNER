# Catatan Progres Pengembangan JuraganAI
## Tanggal: 2026-10-12 | Sesi: #09 | Fase: AI Architecture, Multi-Provider OpenRouter & Floating Chatbot Widget

---

### 1. Tujuan Sesi / Iterasi
1. Menjawab pertanyaan arsitektural seputar guardrails & anti-halusinasi finansial, cakupan fitur F&B, dan letak system prompt.
2. Mengadopsi prinsip AI Engineer dari `.agent/skills/ai-engineer/SKIIL.md` ke dalam arsitektur JuraganAI.
3. Mengintegrasikan multi-provider LLM (**OpenRouter** dan **DeepSeek**) dengan *cascading fallback* (Preferred Provider -> Secondary Provider -> Local Deterministic Engine).
4. Memperbaiki rendering format markdown pada antarmuka web chatbot agar tidak lagi menampilkan karakter mentah (`*`, `-`, `_`), melainkan visual HTML terformat rapi via `marked.js` dan `DOMPurify`.
5. Mentransformasikan chatbot dashboard menjadi **Floating Pop-up Widget** (FAB button di pojok kanan bawah), sekaligus mengoptimalkan ruang dashboard utama untuk panel **Evaluasi Menu & Peringatan Deadstock**.

---

### 2. Pekerjaan yang Selesai Dikerjakan (Done)
- [x] **Adopsi AI Engineer Skill Matrix (`.agent/skills/ai-engineer/SKIIL.md`)**:
  - Menyusun 5 pilar SOP standar: Grounding & Anti-Hallucination, Tool Calling Schemas, Multi-Provider Cascading Fallback, Sliding Window Memory Management, dan UI Presentation & Human-Centered Output.
- [x] **Multi-Provider LLM Engine (`app/core/config.py` & `app/services/deepseek_agent.py`)**:
  - Menambahkan konfigurasi environment: `LLM_PROVIDER` (default: `"deepseek"`, opsi `"openrouter"`), `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL` (`https://openrouter.ai/api/v1`), dan `OPENROUTER_MODEL` (default: `"meta-llama/llama-3.3-70b-instruct:free"`).
  - Menyediakan header standar OpenRouter (`HTTP-Referer` dan `X-Title`).
  - Mengimplementasikan deteksi multi-provider dinamis (`_get_active_providers()`) dan fallback bertingkat:
    1. Provider utama (`LLM_PROVIDER` terpilih).
    2. Provider sekunder (jika provider utama timeout / credit limit).
    3. Local Deterministic Rule-Based Engine (jika koneksi eksternal mati / offline).
- [x] **Temporal Grounding & F&B Recommendation Guidelines**:
  - Memperbarui `_build_system_prompt()` dengan suntikan otomatis nama hari dalam Bahasa Indonesia (contoh: "Senin"), tanggal, dan jam saat ini.
  - Mempertegas arahan jawaban: Setiap analisis wajib menyertakan 1–2 rekomendasi bisnis F&B praktis (saran bundling, mitigasi bahan baku deadstock, penyesuaian staf jam ramai).
- [x] **Rich Markdown Client-Side Rendering (`app/templates/dashboard.html`)**:
  - Menyuntikkan library CDN `marked.min.js` dan `dompurify.min.js`.
  - Mengonversi teks respons bot menggunakan `marked.parse()` dan disanitasi menggunakan `DOMPurify.sanitize()` sehingga tabel, bullet list, font bold, dan heading tampil visual dan elegan.
- [x] **Floating Pop-up Chatbot Widget (`app/templates/dashboard.html`)**:
  - Tombol aksi mengambang (*Floating Action Button / FAB*) melayang di pojok kanan bawah dengan ikon `💬` dan efek gradient hover.
  - Jendela pop-up widget interaktif (`.chat-widget-popup`) dengan header gradasi, badge sesi aktif, tombol reset chat `🔄`, dan tombol tutup `✕`.
  - Layout dashboard utama diperbarui: panel kiri untuk POS Importer CSV/XLSX, dan panel kanan untuk **🏆 Evaluasi Menu & Peringatan Deadstock** lengkap dengan tombol pintasan "💬 Konsultasi AI".

---

### 3. Kendala & Solusi (Issues & Resolutions)
- **Kendala**: Tampilan chatbot sebelumnya menyatu di dashboard grid utama, menyita area monitoring visual, dan karakter markdown seperti `*`, `_`, serta `-` muncul mentah karena format hanya menggunakan `.replace(/\n/g, '<br>')`.
- **Solusi**: 
  - Chatbot dipindahkan ke floating popup widget yang dapat dibuka/tutup kapan saja tanpa menutupi grafik analitik.
  - Client parser menggunakan `marked.parse(text)` yang dibungkus `DOMPurify.sanitize()` untuk keamanan XSS dan tampilan tipografi premium.

---

### 4. Hasil Pengujian / Verifikasi
- Pengujian otomatis via Pytest: **19/19 Passed (100% lulus)**.
- Aplikasi aktif di `http://localhost:8005/` tanpa konflik port.
- Bot Telegram `@JuragangantengAI_bot` tetap sinkron dan merespons query.

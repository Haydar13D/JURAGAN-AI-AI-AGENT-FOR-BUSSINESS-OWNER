# AI Engineer Skill Matrix & Best Practices Guidelines
## Standard Operational Procedure (SOP) untuk Agentic & LLM System Engineering

Dokumen ini mendefinisikan standar keahlian dan panduan teknis seorang **AI Engineer** dalam merancang, membangun, dan memelihara sistem kecerdasan buatan berbasis LLM (Large Language Model), Function Calling, dan Conversational AI.

---

### 1. Grounding & Anti-Hallucination Engineering
- **Deterministic Math & Data Isolation**:
  - LLM tidak boleh dijadikan *calculator* atau *database query writer* secara langsung pada data finansial sensitif.
  - LLM bertindak sebagai *router* dan *natural language synthesizer*. Semua agregasi, pemeringkatan, dan kalkulasi dilakukan oleh engine kode deterministik (Python/SQL).
- **Temporal Anchoring**:
  - Selalu suntikkan konteks tanggal hari ini, hari dalam seminggu, dan jam saat ini (`YYYY-MM-DD HH:MM`) ke dalam System Prompt agar model tidak berhalusinasi tahun/periode.
- **Negative Constraint Guardrails**:
  - Instruksikan larangan keras secara eksplisit: *"DILARANG mengarang angka atau menu jika data tidak ditemukan dalam pemanggilan tool"*.
  - Sediakan instruksi penanganan jika data kosong (graceful empty state).

---

### 2. Tool Calling & Function Schemas Design
- **Strict Typing & Field Boundaries**:
  - Setiap fungsi harus memiliki JSON Schema dengan tipe data eksplisit (`string` dengan format tanggal, `integer` dengan `minimum`/`maximum`, `enum` untuk parameter pilihan).
  - Berikan `description` yang kaya konteks agar LLM memahami kapan dan mengapa tool tersebut harus dipanggil.
- **Atomic Single-Purpose Tools**:
  - Hindari satu fungsi monolitik raksasa. Buat tool terpisah untuk setiap domain analisis (contoh: `get_sales_overview`, `get_top_products`, `get_peak_hours`).
- **Resilient Argument Parsing**:
  - Lakukan `json.loads` dengan `try-catch` dan sanitasi argumen default jika model memberikan argumen yang tidak lengkap.

---

### 3. Multi-Provider Resiliency & Cascading Fallbacks
- **Unified Provider Abstraction**:
  - Manfaatkan antarmuka kompatibel OpenAI SDK untuk berinteraksi dengan multi-provider (DeepSeek, OpenRouter, Ollama, OpenAI).
- **Cascading Fallback Architecture**:
  1. **Tier 1 (Primary)**: Provider utama (misal: DeepSeek Direct API).
  2. **Tier 2 (Secondary)**: Fallback provider (misal: OpenRouter API dengan multi-model routing).
  3. **Tier 3 (Local Deterministic)**: Fallback rule-based NLP parser lokal yang menjalankan tools dan menghasilkan respon terformat tanpa memerlukan koneksi internet/API eksternal.

---

### 4. Context Window & Multi-Turn Memory Management
- **Sliding Window Token Budgeting**:
  - Batasi riwayat chat ke jendela optimal (maksimal 6 pesan / 3 turn terakhir) untuk menjaga latensi tetap rendah dan menghemat token input.
- **Topic Bleeding & Repetition Prevention (Anti-Pattern Contagion)**:
  - LLM rentan menyalin kembali jawaban masa lalu ("Bagian 1: [Topik Kemarin], Bagian 2: [Topik Baru]").
  - Terapkan pemotongan/kompresi (*truncation*) pada pesan `assistant` lampau (>500 karakter) sebelum dimasukkan ke prompt LLM agar tidak mendominasi *attention weights*.
  - Pasang *negative constraint* tegas di system prompt: *"Fokus 100% hanya pada giliran pesan terakhir. Dilarang mengulang konten lama."*
- **Database Message Persistence**:
  - Simpan setiap *turn* (pesan user, balasan assistant, dan metadata tool execution) secara terpisah ke dalam basis data relasional.
- **Session Lifecycle Management**:
  - Sediakan mekanisme reset session eksplisit (`DELETE /conversations/{id}`) agar user dapat mengawali percakapan baru tanpa terkontaminasi konteks lama.

---

### 5. UI Presentation & Human-Centered Output
- **Real-Time Waiting Feedback (Animated Typing Bubble)**:
  - Selalu hadirkan animasi visual titik bergerak (*bouncing typing dots*) saat menunggu respons LLM/tool eksternal agar pengguna tahu sistem sedang memproses dan tidak hang.
- **Rich Markdown to HTML Visual Rendering**:
  - Jangan biarkan pengguna membaca karakter formatting mentah (`*`, `_`, `#`, `-`). Gunakan parser markdown modern (seperti `marked.js` + `DOMPurify`) di sisi client.
- **Actionable Business Insights (F&B Specific)**:
  - Jawaban agent tidak boleh hanya membacakan angka tabel, melainkan harus menyertakan **1–2 rekomendasi bisnis praktis** (contoh: saran promo bundling, mitigasi stok bahan baku, penyesuaian staf saat peak hours).
- **Flexible Interface (Floating Widget vs Full View)**:
  - Berikan fleksibilitas UI: Chatbot sebagai floating popup widget agar area monitoring dashboard tetap luas, namun asisten selalu siap diakses dalam 1 klik.


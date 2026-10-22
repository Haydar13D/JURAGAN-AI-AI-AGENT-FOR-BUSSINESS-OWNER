# ANALISIS PRD KIRATA AI WORKFLOW BUILDER (v2.0)
**Versi PRD**: 2.0 (22 Oktober 2026)  
**Status**: Architecture Alignment & Strategic Analysis  
**Dokumen Asal**: `PRD — Kirata AI Workflow Builder (v2.0)`  

---

## 1. Latar Belakang & Transformasi Produk

### Dari JuraganAI F&B Agent Menjadi Kirata AI Workflow Builder
Sebelumnya, JuraganAI dibangun sebagai agen otomatis tunggal (*single-agent*) khusus untuk UMKM F&B (coffee shop) yang menangani pembacaan data POS, pembuatan flyer promosi, dan publikasi sandbox Instagram.

Melalui **PRD Kirata v2.0**, visi produk berevolusi menjadi:
> **Platform Otomasi Bisnis Berbasis Template yang Konfigurabel (Configurable Automation Platform)**.

| Dimensi | JuraganAI (Lama) | Kirata AI Workflow Builder (Baru) |
| :--- | :--- | :--- |
| **Domain** | F&B Coffee Shop spesifik | Multi-industri berbasis template (Vertical pertama: Retail iPhone Bekas) |
| **Arsitektur Alur** | Hardcoded Pipeline (Fixed Pipeline) | Dynamic Graph berbasis Canonical Definition (JSON) |
| **Peran AI** | Perencana kampanye & copywriter | AI Compiler & Patch Proposer (Mengubah prompt manusia menjadi typed patch graph) |
| **Validasi Eksekusi** | Validasi input data biasa | Server-side DAG Validator (anti-cycle, schema verification, allowlisted tools) |
| **Keamanan Eksekusi** | 1 level approval (Telegram) | Two-Tier Approval (Tier 1: Aktivasi Konfigurasi Graph, Tier 2: Eksekusi Aksi Bisnis) |
| **Transparansi** | Output teks chat | Visual Review First (React Flow Visual Diff + Trace Node Execution) |

---

## 2. Analisis Kunci Persyaratan PRD v2.0

### 2.1 Filosofi Inti: "AI-first • Visual-review-first • Human-approved"
- **AI-first**: Pengguna tidak perlu pusing drag-and-drop puluhan node rumit atau menulis skrip teknis. Cukup sampaikan kebutuhan bisnis dalam bahasa alami (*natural language*).
- **Visual-review-first**: AI tidak boleh menjalankan alur secara "black-box". Setiap usulan perubahan ditampilkan dalam visual graph diff (sebelum vs sesudah) disertai ringkasan dampak risiko (*impact summary*).
- **Human-approved**: Tidak ada alur yang aktif tanpa aktivasi eksplisit, dan **tidak ada mutasi data bisnis nyata (harga / publish) yang dieksekusi tanpa persetujuan tindakan (action approval)**.

### 2.2 First Vertical: Retail Used-iPhone Pricing & Cataloging
Alasan pemilihan vertical ini:
1. **Masalah Nyata & Masif**: Pedagang iPhone bekas setiap hari menerima puluhan daftar harga vendor dalam format CSV/Excel yang acak-acakan (singkatan tidak konsisten, garansi inter vs iBox, kondisi fisik bervariasi).
2. **Kebutuhan Margin Deterministik**: Salah menghitung margin 2-3% saja bisa memicu kerugian jutaan rupiah. LLM tidak boleh menghitung harga secara halusinasi; rumus margin harus 100% matematis pasti.
3. **Batas Ketidakpastian (Transparent Uncertainty)**: Jika data pasar tidak tersedia atau kondisi barang anomali (baterai < 80%), sistem harus menandai `NEEDS_REVIEW`, bukan menebak-nebak harga.

### 2.3 Formula & Kaidah Perhitungan Harga
PRD v2.0 menegaskan aturan perhitungan matematis:
1. **Gross Margin ($m$)**:
   $$m = \frac{\text{Selling Price} - \text{Total Cost}}{\text{Selling Price}}$$
2. **Harga Jual Minimum untuk Margin Target ($m$)**:
   $$\text{Minimum Selling Price} = \frac{\text{Total Cost}}{1 - m} \quad (0 \le m < 1)$$
3. **Total Cost**:
   $$\text{Total Cost} = \text{Vendor Cost} + \text{Overhead/Packaging Fee}$$
4. **Markup vs Margin**:
   $$\text{Markup} = \frac{\text{Selling Price} - \text{Total Cost}}{\text{Total Cost}}$$
   *(Markup dan Margin tidak boleh tertukar!)*
5. **Pembulatan (Rounding)**:
   Menerapkan pembulatan ke atas (*round up*) sesuai kelipatan yang dikonfigurasi (misal kelipatan Rp50.000).
6. **Guardrail Pasar**:
   Jika harga jual minimum yang dihasilkan berada di atas harga pasar pembanding, sistem **TIDAK BOLEH** otomatis memotong harga di bawah margin batas selamat, melainkan wajib diberi label `ABOVE_MARKET / NEEDS_REVIEW`.

---

## 3. Analisis Teknis & Arsitektur Sistem

### 3.1 Canonical Workflow Definition sebagai Single Source of Truth
Banyak sistem gagal karena definisi visual di frontend berbeda dengan kode eksekusi di backend.
Kirata menyelesaikan masalah ini dengan **Canonical JSON Specification**:
```
                 ┌────────────────────────────────┐
                 │  Canonical Workflow Definition │
                 │        (Pydantic / JSON)       │
                 └───────┬────────────────┬───────┘
                         │                │
            Compile to   │                │   Compile to
                         ▼                ▼
            ┌──────────────────┐    ┌──────────────────┐
            │ React Flow Nodes │    │ Executable Node  │
            │  & Visual Edges  │    │ Runner Pipeline  │
            └──────────────────┘    └──────────────────┘
```

### 3.2 Kenapa LLM Hanya Mengeluarkan Typed Patches?
Jika LLM diizinkan menghasilkan kode Python mentah (*arbitrary code execution*):
- Rawan *prompt injection* & kebocoran sistem.
- Sulit di-audit dan di-diff secara visual.
- Rawan galat runtime dan halusinasi dependensi.

Dengan **Typed Patches**:
- LLM hanya memilih operasi terstruktur: `UPDATE_NODE_CONFIG`, `INSERT_NODE`, `REMOVE_EDGE`.
- Setiap node dan tool diverifikasi terhadap *allowlist* resmi.
- Validasi DAG memastikan alur tidak memiliki circular loop (cycle) dan semua parameter terikat dengan benar (*bound*).

### 3.3 Two-Tier Approval System
1. **Tier 1: Configuration Approval (Persetujuan Desain Alur)**
   - Terjadi saat user selesai berdiskusi dengan AI dan melihat *Visual Diff* serta hasil *Dry-Run*.
   - Mengubah status alur dari `DRAFT` menjadi `ACTIVE` versi *immutable* (v1, v2, ...).
2. **Tier 2: Business Action Approval (Persetujuan Tindakan Bisnis Nyata)**
   - Terjadi saat alur kerja sedang mengeksekusi data CSV vendor riil.
   - Pada node `owner_action_approval`, eksekusi berhenti (*PAUSED*) dan mengirimkan kartu approval (di Web dan Telegram) berisi preview tabel harga.
   - Hanya jika di-*approve*, data diekspor ke katalog final.

---

## 4. Evaluasi & Kesimpulan Strategis

Arsitektur PRD v2.0 Kirata AI Workflow Builder sangat solid, modular, dan memitigasi risiko kegagalan AI umum di ranah bisnis:
1. **Reliability**: Deterministic math + DAG validation menjamin keandalan sistem.
2. **Safety**: Two-tier approval menjamin nol aksi mutasi tanpa izin owner.
3. **Extensibility**: Modul F&B yang sudah dibuat sebelumnya tidak sia-sia, melainkan siap dimasukkan ke dalam template registry sebagai template kedua setelah template retail iPhone ini rampung.

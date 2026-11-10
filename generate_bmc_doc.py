import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

def set_cell_background(cell, fill_hex):
    tcPr = cell._tc.get_or_add_tcPr()
    tcPr.append(parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>'))

def set_cell_margins(cell, top=100, bottom=100, left=120, right=120):
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement('w:tcMar')
    for m, val in [('top', top), ('bottom', bottom), ('left', left), ('right', right)]:
        node = OxmlElement(f'w:{m}')
        node.set(qn('w:w'), str(val))
        node.set(qn('w:type'), 'dxa')
        tcMar.append(node)
    tcPr.append(tcMar)

def create_bmc_document():
    doc = docx.Document()

    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)

    NAVY = RGBColor(26, 44, 76)
    BLUE = RGBColor(37, 99, 235)
    CHARCOAL = RGBColor(30, 41, 59)
    MUTED = RGBColor(100, 116, 139)

    # Title
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = Pt(0)
    p_title.paragraph_format.space_after = Pt(2)
    r_title = p_title.add_run("BUSINESS MODEL CANVAS (BMC) & ANALISIS PROGRES")
    r_title.font.name = "Arial"
    r_title.font.size = Pt(20)
    r_title.font.bold = True
    r_title.font.color.rgb = NAVY

    # Subtitle
    p_sub = doc.add_paragraph()
    p_sub.paragraph_format.space_after = Pt(12)
    r_sub = p_sub.add_run("Kirata AI Workflow Builder — Platform Otomasi Bisnis Berbasis Template & Human-in-the-Loop")
    r_sub.font.name = "Arial"
    r_sub.font.size = Pt(11)
    r_sub.font.bold = True
    r_sub.font.color.rgb = BLUE

    # Callout Metadata
    tbl_meta = doc.add_table(rows=1, cols=1)
    tbl_meta.alignment = WD_TABLE_ALIGNMENT.CENTER
    c_meta = tbl_meta.cell(0, 0)
    set_cell_background(c_meta, "F8FAFC")
    set_cell_margins(c_meta, top=120, bottom=120, left=160, right=160)
    p_meta = c_meta.paragraphs[0]
    p_meta.paragraph_format.space_after = Pt(0)
    r_meta = p_meta.add_run(
        "Status Progres: Sesi #14 Selesai (Persistent Memory & Full Test Suite 19/19 PASSED)\n"
        "Vertical Aktif: 1. Retail Gadget / iPhone Bekas | 2. F&B Sales-to-Marketing Automation\n"
        "Tanggal Dokumen: 02 November 2026 | Kategori: Model Bisnis & Laporan Status Teknis"
    )
    r_meta.font.name = "Arial"
    r_meta.font.size = Pt(9.5)
    r_meta.font.color.rgb = CHARCOAL

    doc.add_paragraph().paragraph_format.space_after = Pt(8)

    def add_h1(text):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(14)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.keep_with_next = True
        run = p.add_run(text)
        run.font.name = "Arial"
        run.font.size = Pt(13)
        run.font.bold = True
        run.font.color.rgb = NAVY
        return p

    def add_h2(text):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.keep_with_next = True
        run = p.add_run(text)
        run.font.name = "Arial"
        run.font.size = Pt(11)
        run.font.bold = True
        run.font.color.rgb = BLUE
        return p

    def add_p(text, bold_prefix=None, space_after=5):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(space_after)
        p.paragraph_format.line_spacing = 1.15
        if bold_prefix:
            r_pre = p.add_run(bold_prefix)
            r_pre.font.name = "Arial"
            r_pre.font.size = Pt(9.5)
            r_pre.font.bold = True
            r_pre.font.color.rgb = CHARCOAL
        run = p.add_run(text)
        run.font.name = "Arial"
        run.font.size = Pt(9.5)
        run.font.color.rgb = CHARCOAL
        return p

    # --- SECTION 1: ANALISIS PROGRES TERBARU ---
    add_h1("1. ANALISIS STATUS & PROGRES TERBARU SISTEM (Sesi #14)")
    add_p(
        "Berdasarkan penelusuran arsitektur dan riwayat pengembangan pada direktori f:\\AI-AGENT, sistem Kirata AI Workflow Builder "
        "telah melampaui fase rancangan konsep dan saat ini telah berstatus 'Production-Ready MVP' yang beroperasi secara penuh di lingkungan pengujian."
    )
    add_p(
        "Pencapaian Kunci Fase Terkini (Sesi #12 s.d. #14):",
        bold_prefix="Milestone yang Telah Diselesaikan: "
    )
    add_p(
        "1. Arsitektur Canonical Workflow & Server-side DAG Validator: Menerapkan definisi JSON tunggal untuk visualisasi frontend dan eksekusi backend. Validasi alur menggunakan algoritma Kahn membuktikan sistem kebal terhadap siklus (infinite loop), tool tak terdaftar, dan parameter tanpa binding.\n"
        "2. Multi-Vertical Templates Tersertifikasi: \n"
        "   - Template #1 (Retail Gadget / iPhone Bekas): 9 node fungsional untuk normalisasi daftar vendor (inter vs iBox), matching harga pasar, perhitungan margin matematis (zero math hallucination), dan review guardrail baterai/anomali.\n"
        "   - Template #2 (F&B Sales-to-Marketing): Deteksi penurunan produk POS kasir, perencanaan kampanye otomatis, dan rendering flyer promosi SVG 1080x1080.\n"
        "3. Persistent Execution Memory & History Audit (Sesi #14): Mengganti in-memory volatile dengan basis data SQLite/SQLAlchemy. Setiap run mencatat input_snapshot dan output_snapshot per node, keputusan reviewer, dan jejak audit.\n"
        "4. Kestabilan Test Suite 100%: Sebanyak 19 pengujian otomatis (unit & integration tests) dinyatakan lulus sempurna (19 Passed in 22.78s), membuktikan kehandalan API, runtime, dan persetujuan aksi manusia."
    )

    # --- SECTION 2: 9 BLOK BUSINESS MODEL CANVAS ---
    add_h1("2. THE 9 BLOCKS OF BUSINESS MODEL CANVAS (KIRATA AI)")
    add_p(
        "Berikut adalah pemetaan model bisnis terstruktur ke dalam 9 blok kanvas kanonikal untuk Kirata AI Workflow Builder:"
    )

    bmc_table = doc.add_table(rows=1, cols=3)
    bmc_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    col_w = [2.2, 2.3, 2.3]

    headers = ["Kategori Blok BMC", "Komponen Strategis Kirata AI", "Detail Implementasi Operasional"]
    hdr_cells = bmc_table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = h
        set_cell_background(hdr_cells[i], "1A2C4C")
        set_cell_margins(hdr_cells[i], top=100, bottom=100, left=120, right=120)
        p = hdr_cells[i].paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        for r in p.runs:
            r.font.name = "Arial"
            r.font.size = Pt(9.5)
            r.font.bold = True
            r.font.color.rgb = RGBColor(255, 255, 255)

    bmc_data = [
        [
            "1. Value Propositions\n(Proporsi Nilai)",
            "• Zero-Code Natural Language Configuration\n• Deterministic Zero-Math-Hallucination\n• Two-Tier Safety Approval\n• Pangkas Waktu 2 Jam -> 30 Detik",
            "Menawarkan otomasi bisnis tanpa pusing coding atau drag-and-drop rumit. Menggaransi akurasi 100% rumus margin/keuangan (dihitung Python deterministik). Menjaga keamanan data dengan menahan aksi eksternal sampai pemilik menekan tombol 'Approve'."
        ],
        [
            "2. Customer Segments\n(Segmen Pelanggan)",
            "• Pedagang Retail iPhone / Gadget Second\n• Pemilik Kedai Kopi / Kafe Independen\n• Solo Entrepreneur / SMB Non-Teknis",
            "Fokus utama: Pedagang retail gadget mandiri (1-3 toko) yang menerima puluhan daftar vendor acak setiap pagi di WA, serta pemilik F&B yang memiliki data transaksi POS namun tidak sempat menganalisis penurunan omzet menu."
        ],
        [
            "3. Channels\n(Saluran Distribusi)",
            "• Web Dashboard (/kirata)\n• Telegram Bot (@JuragangantengAI_bot)\n• Direct WhatsApp Concierge",
            "Distribusi berbasis cloud SaaS instan. Interaksi harian dilakukan via antarmuka web modern atau bot percakapan Telegram tanpa memerlukan instalasi aplikasi yang berat."
        ],
        [
            "4. Customer Relationships\n(Hubungan Pelanggan)",
            "• Self-Service Template-First\n• Concierge Onboarding Awal\n• Automated Daily Delivery & Alerts",
            "Pengguna memulai dari template yang sudah teruji. Di tahap awal disediakan asistensi pemetaan format vendor secara langsung. Hubungan jangka panjang dibangun lewat laporan harian proaktif yang tiba setiap pagi/malam."
        ],
        [
            "5. Revenue Streams\n(Arus Pendapatan)",
            "• Starter Tier: Rp 79.000 / bulan\n• Pro Tier: Rp 149.000 / bulan\n• One-time Setup / Concierge: Rp 250.000\n• Diskon Paket Tahunan (Hemat 2 Bulan)",
            "Pendapatan berulang bulanan (MRR) berbasis langganan SaaS. Margin kotor diproyeksikan sangat tinggi (>90%) karena efisiensi inferensi model bahasa dan komputasi deterministik internal."
        ],
        [
            "6. Key Activities\n(Aktivitas Kunci)",
            "• Pemeliharaan Runtime & DAG Validator\n• Kurasi Dataset Referensi Pasar Berkala\n• Pengembangan Template Industri Baru\n• Customer Support & Edukasi Pengguna",
            "Menjaga kehandalan alur kerja, memvalidasi stabilitas API DeepSeek/AtmoRouter, memperbarui acuan harga pasar gadget, dan memastikan onboarding pengguna baru berjalan di bawah 5 menit."
        ],
        [
            "7. Key Resources\n(Sumber Daya Kunci)",
            "• Proprietary Tool Registry & Schemas\n• Certified Domain Tools (Retail & F&B)\n• Server Linux Lokal Proxmox / Cloud VPS\n• Basis Data Snapshot & Jejak Audit",
            "Aset intelektual berupa kontrak alat terstandarisasi, library normalisasi singkatan vendor lokal, arsitektur single-source-of-truth, dan infrastruktur komputasi hemat daya."
        ],
        [
            "8. Key Partnerships\n(Kemitraan Kunci)",
            "• Penyedia LLM (DeepSeek, OpenRouter)\n• Komunitas / Asosiasi Pedagang Gadget\n• Penyedia Ekosistem POS Kasir\n• Cloud & Domain Provider (Cloudflare)",
            "Bekerja sama dengan komunitas pedagang gadget lokal untuk akuisisi merchant organik, serta memanfaatkan provider AI cost-effective untuk menjaga biaya inferensi tetap rendah."
        ],
        [
            "9. Cost Structure\n(Struktur Biaya)",
            "• Token LLM API (DeepSeek V3): Sangat hemat\n• Biaya Listrik & Konektivitas Server\n• Domain, SSL & Cloud VPS Backup\n• Operasional & Legalitas Usaha",
            "Struktur biaya ramping (lean). Biaya langsung (COGS) per tenant aktif di bawah Rp 6.500/bulan, memungkinkan titik impas (Break-Even Point) tercapai hanya dengan 21 pengguna berbayar."
        ]
    ]

    for r_idx, row_data in enumerate(bmc_data):
        row_cells = bmc_table.add_row().cells
        bg_color = "F8FAFC" if r_idx % 2 == 1 else "FFFFFF"
        for c_idx, val in enumerate(row_data):
            row_cells[c_idx].text = str(val)
            set_cell_background(row_cells[c_idx], bg_color)
            set_cell_margins(row_cells[c_idx], top=80, bottom=80, left=120, right=120)
            p = row_cells[c_idx].paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            for r in p.runs:
                r.font.name = "Arial"
                r.font.size = Pt(9.0)
                r.font.color.rgb = CHARCOAL

    for row in bmc_table.rows:
        for i, w in enumerate(col_w):
            row.cells[i].width = Inches(w)

    doc.add_paragraph().paragraph_format.space_after = Pt(8)

    # --- SECTION 3: ANALISIS KELAYAKAN & KEUNGGULAN KOMPETITIF ---
    add_h1("3. KEUNGGULAN STRATEGIS & KESIMPULAN BISNIS")
    add_p(
        "1. Diferensiasi Produk Kuat: Tidak bersaing di kategori 'General AI Chat' yang rentan halusinasi, melainkan di kategori 'Reliable Execution Engine'. Angka finansial dijamin akurat oleh aturan Python deterministik.\n"
        "2. Solusi Nyata untuk Non-IT (Iceberg Paradigm): Menghilangkan kerumitan visual graph dari operasional sehari-hari. Pemilik toko hanya perlu mengunggah file dan menekan satu tombol 'Approve', sementara seluruh kompleksitas DAG berjalan di balik layar.\n"
        "3. Kesiapan Komersial: Dengan tuntasnya sistem snapshot memory dan jejak audit di Sesi #14, Kirata AI siap masuk ke tahap pilot pengujian lapangan bersama merchant mitra nyata."
    )

    out_file = "f:/AI-AGENT/Kirata_AI_Business_Model_Canvas.docx"
    doc.save(out_file)
    print(f"File BMC berhasil dibuat di: {out_file}")

if __name__ == "__main__":
    create_bmc_document()

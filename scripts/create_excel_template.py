import os
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

def generate_template():
    wb = openpyxl.Workbook()
    
    # -------------------------------------------------------------
    # SHEET 1: Input Transaksi POS
    # -------------------------------------------------------------
    ws1 = wb.active
    ws1.title = "Transaksi_POS"
    ws1.views.sheetView[0].showGridLines = True

    # Styling colors
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid") # Dark Navy
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    accent_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid") # Soft amber
    
    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )
    
    headers = [
        "No Struk", 
        "Tanggal Transaksi", 
        "Kategori", 
        "Nama Menu", 
        "Jumlah", 
        "Harga Satuan", 
        "Diskon", 
        "Total", 
        "Metode Pembayaran", 
        "Nama Kasir", 
        "Status"
    ]

    # Write Headers at row 1
    header_row = 1
    ws1.row_dimensions[header_row].height = 26
    for col_num, header in enumerate(headers, 1):
        cell = ws1.cell(row=header_row, column=col_num)
        cell.value = header
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border

    # Sample Data Rows
    sample_data = [
        ("INV-20261013-001", "2026-10-13 09:15:00", "Kopi", "Kopi Susu Gula Aren", 2, 22000, 0, 44000, "QRIS", "Budi", "Completed"),
        ("INV-20261013-001", "2026-10-13 09:15:00", "Pastry", "Croissant Butter", 1, 25000, 0, 25000, "QRIS", "Budi", "Completed"),
        ("INV-20261013-002", "2026-10-13 10:30:00", "Makanan", "Nasi Goreng Spesial", 1, 35000, 5000, 30000, "Tunai", "Siti", "Completed"),
        ("INV-20261013-002", "2026-10-13 10:30:00", "Non-Kopi", "Matcha Latte Ice", 1, 28000, 0, 28000, "Tunai", "Siti", "Completed"),
        ("INV-20261013-003", "2026-10-13 12:45:00", "Makanan", "Mie Goreng Pedas", 2, 30000, 0, 60000, "Debit", "Budi", "Completed"),
        ("INV-20261013-003", "2026-10-13 12:45:00", "Cemilan", "Kentang Goreng Keju", 1, 20000, 0, 20000, "Debit", "Budi", "Completed"),
        ("INV-20261013-004", "2026-10-13 14:10:00", "Kopi", "Caffe Latte", 1, 26000, 0, 26000, "QRIS", "Siti", "Completed"),
        ("INV-20261013-005", "2026-10-13 19:20:00", "Makanan", "Ayam Geprek Sambal Matah", 3, 32000, 0, 96000, "QRIS", "Budi", "Completed"),
        ("INV-20261013-005", "2026-10-13 19:20:00", "Kopi", "Americano Ice", 2, 20000, 0, 40000, "QRIS", "Budi", "Completed"),
    ]

    for row_idx, row_data in enumerate(sample_data, header_row + 1):
        ws1.row_dimensions[row_idx].height = 20
        for col_idx, val in enumerate(row_data, 1):
            cell = ws1.cell(row=row_idx, column=col_idx)
            cell.value = val
            cell.font = Font(name="Calibri", size=10)
            cell.border = thin_border
            
            # Alignments & Number formats
            if col_idx in [1, 2, 10, 11]: # No Struk, Waktu, Kasir, Status
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif col_idx in [3, 4, 9]: # Kategori, Menu, Metode
                cell.alignment = Alignment(horizontal="left", vertical="center")
            elif col_idx == 5: # Qty
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.number_format = "#,##0"
            elif col_idx in [6, 7, 8]: # Harga, Diskon, Total
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.number_format = "#,##0"

    # Auto-adjust column widths
    for col in ws1.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws1.column_dimensions[col_letter].width = max(max_len + 5, 14)

    # -------------------------------------------------------------
    # SHEET 2: Petunjuk Pengisian & FAQ Database
    # -------------------------------------------------------------
    ws2 = wb.create_sheet(title="Petunjuk_Pengisian")
    ws2.views.sheetView[0].showGridLines = True
    
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 30
    ws2.column_dimensions["C"].width = 50

    # Header Sheet 2
    ws2.merge_cells("A1:C1")
    s2_title = ws2["A1"]
    s2_title.value = "📋 PANDUAN PENGISIAN & KAMUS KOLOM JURAGAN AI"
    s2_title.font = Font(name="Calibri", size=14, bold=True, color="1E293B")
    s2_title.alignment = Alignment(horizontal="left", vertical="center")
    ws2.row_dimensions[1].height = 26

    guide_headers = ["Nama Kolom", "Format yang Didukung", "Keterangan & Tips"]
    ws2.row_dimensions[3].height = 24
    for c_idx, h_text in enumerate(guide_headers, 1):
        c = ws2.cell(row=3, column=c_idx)
        c.value = h_text
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = thin_border

    guides = [
        ("No Struk", "Teks (bebas, misal: INV-001, STRUK-123)", "Wajib. Jika 1 struk berisi 3 menu, tuliskan No Struk yang sama untuk ketiga baris tersebut. Sistem otomatis menggabungkannya jadi 1 transaksi."),
        ("Tanggal Transaksi", "YYYY-MM-DD HH:MM:SS atau DD/MM/YYYY HH:MM", "Wajib. Tanggal dan jam transaksi. Jam sangat penting untuk analitik Jam Ramai (Peak Hours)."),
        ("Kategori", "Teks (Kopi, Makanan, Cemilan, Pastry, Non-Kopi)", "Kategori produk. Jika menu baru belum ada kategorinya, sistem otomatis mendaftarkannya."),
        ("Nama Menu", "Teks (Kopi Susu, Nasi Goreng, dll)", "Nama menu. Digunakan untuk analitik Best-Sellers (terlaris) dan Deadstock (sepi peminat)."),
        ("Jumlah (Qty)", "Angka bulat (1, 2, 5, ...)", "Jumlah cup / porsi yang dibeli pada baris menu tersebut."),
        ("Harga Satuan", "Angka (tanpa Rp / titik)", "Harga per porsi menu."),
        ("Diskon", "Angka (0 jika tidak ada)", "Potongan harga khusus untuk baris menu ini atau struk."),
        ("Total", "Angka = (Jumlah * Harga) - Diskon", "Total nominal yang dibayar untuk menu tersebut."),
        ("Metode Pembayaran", "QRIS, Tunai, Debit, Kartu Kredit, Transfer", "Digunakan untuk analisis metode pembayaran paling disukai pelanggan."),
        ("Nama Kasir", "Teks (Budi, Siti, Kasir 1)", "Nama staf kasir yang melayani (opsional)."),
        ("Status", "Completed, Selesai, Lunas, Refund, Void", "Default 'Completed'. Transaksi berstatus Refund/Void tidak dihitung ke omzet bersih.")
    ]

    for r_idx, g in enumerate(guides, 4):
        ws2.row_dimensions[r_idx].height = 36
        for col_idx, txt in enumerate(g, 1):
            cell = ws2.cell(row=r_idx, column=col_idx)
            cell.value = txt
            cell.font = Font(name="Calibri", size=10)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # FAQ Section
    faq_start = len(guides) + 6
    ws2.merge_cells(f"A{faq_start}:C{faq_start}")
    faq_title = ws2.cell(row=faq_start, column=1)
    faq_title.value = "💡 FAQ: APAKAH PERBEDAAN TEMPLATE MEMPENGARUHI DATABASE?"
    faq_title.font = Font(name="Calibri", size=12, bold=True, color="D97706")
    ws2.row_dimensions[faq_start].height = 24

    faq_content = [
        ("T: Apakah template data tiap vendor POS berbeda-beda?", 
         "J: Benar. Setiap aplikasi POS (Moka, Pawoon, Majoo, ESB, Olsera) memiliki penamaan kolom yang berbeda saat di-export."),
        ("T: Jika saya upload template yang berbeda, apakah merusak database?", 
         "J: TIDAK SAMA SEKALI. JuraganAI memiliki 'Smart Normalization Layer' (Kamus Alias). Mau header-nya bernama 'No Struk', 'Receipt No', atau 'Order ID', sistem otomatis memetakannya ke database dengan standar yang sama."),
        ("T: Bagaimana jika file yang sama di-upload dua kali?", 
         "J: Sistem memiliki proteksi deduplikasi otomatis berdasarkan 'No Struk' dan tanggal. Data tidak akan terhitung ganda (aman).")
    ]

    for offset, (q, a) in enumerate(faq_content, 1):
        cur_row = faq_start + offset
        ws2.row_dimensions[cur_row].height = 28
        ws2.merge_cells(f"A{cur_row}:C{cur_row}")
        c = ws2.cell(row=cur_row, column=1)
        c.value = f"{q}\n{a}"
        c.font = Font(name="Calibri", size=10)
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # Save output
    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "synthetic"))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "template_input_transaksi_juragan_ai.xlsx")
    wb.save(out_path)
    print(f"Template berhasil dibuat di: {out_path}")
    
    # Simpan juga di folder root agar mudah diakses
    root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "template_input_transaksi_juragan_ai.xlsx"))
    wb.save(root_path)
    print(f"Salinan root dibuat di: {root_path}")

if __name__ == "__main__":
    generate_template()

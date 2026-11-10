# Master Index Fixtures: Real UMKM Datasets

Panduan alokasi dataset real-world UMKM:
- **`dev/` (12 Fixtures Target)**: Digunakan untuk memandu pembuatan `DataProfilerService`, unit tests regex/Rupiah parsing, dan tuning confidence mapper.
- **`held_out/` (8 Fixtures Target)**: Disimpan terisolasi, TIDAK PERNAH dilihat oleh pengembang/model selama pembuatan kode sampai tahap Milestone 5 (Final Acceptance).

---

## Daftar Fixture Aktif

| ID Fixture | Tipe Usaha | Format File | Dev / Held-Out | Catatan Keberantakan & Karakteristik Data |
| :--- | :--- | :--- | :--- | :--- |
| `fixture_01_dashboard_export` | F&B / Burger Bar | `.xlsx` (multi-sheet) | `dev` | • Sheet 1: `Dashboard` (Metrik agregat ringkas).<br>• Sheet 2: `Daily Report` berisi multi-tabel horizontal berdampingan (Penjualan, Rekap Hari, Kategori, Menu Terlaris) dipisah kolom kosong `D`, `G`, `L`. |
| `fixture_02_kartu_stok_bahan` | F&B / Burger Bar | `.csv` (log mutasi kartu stok) | `dev` | • Buku mutasi transaksi per order (`Pesanan #ORD-...`).<br>• Angka kuantitas bercampur satuan teks (`910.00 pcs`, `420.00 ml`).<br>• Kolom mutasi bertanda minus `-` jika kosong.<br>• Data berulang per bahan, perlu agregasi status stok terkini (`Stok Akhir` terakhir per `Kode`). |

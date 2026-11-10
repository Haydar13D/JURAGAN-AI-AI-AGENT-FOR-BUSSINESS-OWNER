# Template & Log Wawancara UMKM: Week 0 Discovery

> **Tujuan:** Mengumpulkan data mentah, proses nyata, format file catatan stok, serta keraguan pemilik usaha terkait automasi restock dan pemesanan supplier.

---

## Profil Responden

- **Nama Usaha / Toko**: [Contoh: Kopi Senja / Toko Kelontong Berkah]
- **Jenis Usaha**: [F&B / Retail / Mini-market / Bakery]
- **Narasumber & Peran**: [Owner / Kepala Toko / Admin Gudang]
- **Tanggal Wawancara**: [DD-MM-YYYY]
- **Jumlah SKU / Item Aktif**: [Estimasi: 20-50 SKU / 100+ SKU / 500+ SKU]

---

## Panduan Pertanyaan & Catatan Lapangan

### 1. Sumber Data & Pencatatan Stok
> *"Stok sekarang dicatat di mana dan bagaimana (Excel, buku, POS, WA)?"*

- **Media yang digunakan**: [ ] Buku Tulis Manual  [ ] Excel/Spreadsheet  [ ] POS/Kasir  [ ] Campuran
- **Frekuensi Update**: [ ] Real-time transaksi  [ ] Harian tutup toko  [ ] Mingguan saat mau belanja
- **Detail Proses & Catatan Lapangan**:
  *(Tuliskan detail cara mereka mencatat di sini...)*

---

### 2. File & Format Catatan (Artefak Fixture)
> *"Boleh lihat file atau catatannya? (dengan persetujuan & nama disamarkan)"*

- **Izin Berbagi File**: [ ] Ya, dapat file digital  [ ] Ya, foto buku catatan  [ ] Tidak diizinkan
- **Path Fixture yang Disimpan**: `tests/fixtures/real_umkm/dev/fixture_01_nama.xlsx`
- **Karakteristik Keberantakan File (Checklist Profiler)**:
  - [ ] Ada cell yang dimerge (gabungan baris/kolom)
  - [ ] Baris judul/header bukan di baris paling atas (baris 1)
  - [ ] Angka bercampur format teks rupiah (contoh: `Rp 15.000` atau `15k`)
  - [ ] Ada desimal dengan tanda koma (contoh: `12,5 kg`)
  - [ ] Nama kolom ambigu (contoh: `Sisa`, `Keluar`, `Ket`, `Out`)
  - [ ] Ada baris `Total` / `Rekapitulasi` di bagian bawah tabel
- **Catatan Tambahan**:
  *(Tuliskan keunikan format file di sini...)*

---

### 3. Logika & Batas Restock
> *"Kapan dan bagaimana kamu memutuskan harus restock? Ada angka batasnya?"*

- **Aturan Ambang Batas**:
  - [ ] Punya angka minimal baku (Safety stock tetap, misal: sisa $\le 5$ karton)
  - [ ] Menghitung kebutuhan perkiraan hari (misal: stok cukup untuk 3 hari ke depan)
  - [ ] Pakai feeling / intuisi tanpa batasan angka tertulis
- **Perlakuan Khusus**: [ ] Ada perbedaan buffer menjelang weekend/libur  [ ] Sama rata setiap hari
- **Detail Logika**:
  *(Tuliskan cara perhitungan mereka menentukan jumlah yang harus dibeli...)*

---

### 4. Proses Pemesanan ke Supplier
> *"Siapa yang memesan ke supplier, lewat apa, dan berapa lama prosesnya tiap minggu?"*

- **Penanggung Jawab Order**: [ ] Owner langsung  [ ] Admin/Staf toko (perlu izin owner)
- **Kanal Pemesanan**: [ ] WhatsApp chat santai  [ ] WhatsApp format template  [ ] Telepon  [ ] Sales keliling
- **Durasi Waktu Tiap Minggu**: [ ] < 30 menit  [ ] 1–2 jam  [ ] 3+ jam
- **Contoh Chat Pesanan Asli (Disamarkan)**:
  ```text
  (Tempel contoh format pesan yang biasa dikirim ke supplier di sini)
  ```

---

### 5. Dampak Kesalahan Stok (Valuasi Masalah)
> *"Pernah salah pesan atau kehabisan stok? Dampaknya berapa?"*

- **Pernah Kehabisan Stok (Stockout)**:
  - Frekuensi: [ ] Jarang  [ ] Sebulan 1-2 kali  [ ] Sering
  - Dampak Omset Hilang: Rp ________________ / kejadian
- **Pernah Kelebihan Stok (Overstock / Basi / Expired)**:
  - Dampak Kerugian: Rp ________________
- **Cerita Nyata**:
  *(Tuliskan pengalaman buruk mereka saat stok kacau...)*

---

### 6. Hambatan & Keraguan Adopsi Bot Telegram
> *"Kalau ada bot Telegram yang menyiapkan draf pesanan, kamu bakal pakai? Apa yang bikin ragu?"*

- **Tingkat Minat**: [ ] Sangat Tertarik  [ ] Ragu-ragu  [ ] Menolak
- **Faktor yang Bikin Ragu / Takut**:
  - [ ] Takut salah hitung / salah pesan
  - [ ] Tidak terbiasa pakai Telegram (lebih suka WA)
  - [ ] Takut data penjualan/omset bocor
  - [ ] Terlalu ribet jika harus upload file tiap minggu
- **Komentar Langsung Narasumber**:
  *(Kutip perkataan asli pemilik usaha di sini...)*

---

## Kesimpulan & Action Item Tim Kirata
- **Kelayakan untuk Use Case Restock**: [Sangat Cocok / Kurang Cocok]
- **Daftar Istilah / Kolom Khusus Usaha Ini**:
  - `Nama Kolom di File` $\rightarrow$ `Maksud Sebenarnya`
- **Tindak Lanjut**: [ ] Masukkan file ke Dev Fixture  [ ] Masukkan ke Held-out Test Fixture


# Panduan AI Image Generation untuk Flyer Promosi F&B (JuraganAI)

Dokumen ini menjelaskan opsi integrasi model kecerdasan buatan (AI) untuk menghasilkan materi promosi visual (flyer Instagram 1080x1080) yang menarik, mulai dari opsi **100% Gratis (Free)** hingga **Model Premium Berbayar (Commercial Grade)**.

---

## 1. Ringkasan & Tabel Perbandingan Model

| Model / Provider | Tipe Biaya | Estimasi Biaya per Gambar | Kemampuan Render Teks (Typography) | Kualitas Visual F&B | Rekomendasi Penggunaan |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **1. SVG Template Engine (Bawaan)** | **Gratis** | **Rp 0** (Lokal) | Sempurna (100% Tajam via Kode) | Grafis Vektor Modern | Default MVP, zero-cost, server offline, andal tanpa API eksternal. |
| **2. Pollinations.ai (Flux Backend)** | **Gratis** | **Rp 0** (Tanpa API Key) | Kurang (Teks sering abstrak) | Sangat Tinggi (Fotorealistik) | Eksperimen gratis, showcase foto produk estetik tanpa biaya langganan. |
| **3. Hugging Face Serverless API** | **Gratis** | **Rp 0** (Free Token) | Terbatas | Tinggi (SDXL / Flux Schnell) | Opsi free tier kedua dengan akun pengembang Hugging Face. |
| **4. Flux.1 Schnell / Dev via Together AI / Fal.ai** | **Berbayar Murah** | **~$0.003 - $0.015** (~Rp50 - Rp240) | Cukup Baik | Luar Biasa (High-end photorealism) | Pilihan terbaik untuk skala produksi dengan efisiensi biaya maksimal. |
| **5. OpenAI DALL-E 3** | **Berbayar Standard** | **$0.040** (~Rp640) | Baik (1-2 kata jelas) | Sangat Bagus & Kreatif | Praktis jika sudah memiliki saldo OpenAI API (`OPENAI_API_KEY`). |
| **6. Ideogram v2 API** | **Berbayar Khusus Poster** | **~$0.080** (~Rp1.280) | **TERBAIK DI DUNIA** (Teks poster tidak typo) | Sangat Tinggi (Desain Poster Lengkap) | **Rekomendasi Utama untuk Flyer Poster**: Bisa menulis diskon, harga, & menu dengan rapi. |
| **7. Recraft v3** | **Berbayar Desain Grafis** | **~$0.040** (~Rp640) | Sangat Baik | Vektor & Ilustrasi Brand Unik | Sangat cocok untuk brand kopi indie dengan gaya visual artistik/retro. |

---

## 2. Detail Opsi Model

### Opsi A: 100% Gratis (Free Tier)

#### 1. Pollinations.ai (Flux / SDXL)
- **Cara Kerja:** Layanan API publik tanpa perlu registrasi atau API key. Menggunakan model open-source Flux di backend.
- **URL Endpoint:**
  ```text
  https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1080&model=flux&nologo=true
  ```
- **Kelebihan:** 
  - Nol biaya API (gratis selamanya).
  - Foto makanan dan minuman terlihat sangat menggugah selera (*appetizing*).
- **Kekurangan:**
  - Terkadang server publik mengalami antrean (mengembalikan HTTP 503 saat traffic tinggi). JuraganAI telah dilengkapi *automatic fallback* ke format SVG jika hal ini terjadi.
  - Teks promosi sebaiknya tidak dicetak di gambar, melainkan diletakkan pada caption atau di-overlay secara grafis.

#### 2. Hugging Face Inference API
- **Model:** `black-forest-labs/FLUX.1-schnell` atau `stabilityai/stable-diffusion-xl-base-1.0`.
- **Kelebihan:** Stabil dan didukung oleh infrastruktur open-source terbesar.
- **Kekurangan:** Memerlukan akun Hugging Face dan ada batasan rate limit per jam pada akun free.

---

### Opsi B: Berbayar Sangat Terjangkau (Best Value for Production)

#### 3. Flux.1 Schnell / Dev via Together AI atau Fal.ai
- **Biaya:** Sekitar Rp50 s/d Rp250 per gambar. Dengan modal Rp10.000, Anda bisa menghasilkan 40–100 materi promosi.
- **Kelebihan:** 
  - Kecepatan render sangat tinggi (< 3 detik).
  - Pencahayaan (*cinematic lighting*), pantulan kaca cangkir kopi, dan uap panas terlihat sangat nyata (*photorealistic*).
- **Cara Integrasi:**
  Menggunakan SDK OpenAI-compatible atau REST API dari Together AI:
  ```python
  from together import Together
  client = Together(api_key="together_api_key")
  response = client.images.generate(
      prompt=prompt,
      model="black-forest-labs/FLUX.1-schnell",
      width=1080,
      height=1080,
      steps=4
  )
  ```

---

### Opsi C: Model Premium Khusus Poster Promosi

#### 4. Ideogram v2 API (Rekomendasi Utama Desain Poster)
- **Mengapa Ideogram Berbeda?**  
  Mayoritas model AI (seperti Stable Diffusion dan Midjourney) mengalami kesulitan merender teks tulisan—seringkali huruf menjadi acak-acakan (*gibberish*).  
  **Ideogram v2 dirancang khusus untuk Tipografi & Layout Desain Grafis.**
- **Kemampuan:** 
  Dapat menghasilkan poster dengan teks presisi seperti *"KOPI KENANGAN SENJA"*, *"AFTERNOON PROMO DISKON 15%"*, dan *"Rp 27.200"*.
- **Endpoint API:** `https://api.ideogram.ai/generate`
- **Biaya:** ~$0.08 per poster beresolusi tinggi.

#### 5. OpenAI DALL-E 3
- **Kelebihan:** 
  - Prompt adherence sangat patuh pada instruksi kompleks.
  - Sangat mudah dipasang jika sudah menggunakan ekosistem OpenAI.
- **Cara Integrasi:**
  ```python
  from openai import AsyncOpenAI
  client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
  result = await client.images.generate(
      model="dall-e-3",
      prompt=prompt,
      size="1024x1024",
      quality="standard"
  )
  ```

---

## 3. Cara Mengaktifkan Opsi AI Flyer di JuraganAI

Dalam kode `app/services/flyer_generator.py` dan `app/services/campaign_planner.py`, engine flyer dapat diganti kapan saja via konfigurasi environment:

### Di File `.env`:
```env
# Pilihan Engine Flyer: 'svg' (default free), 'pollinations' (free flux), atau 'dalle3' (berbayar)
FLYER_IMAGE_ENGINE=svg

# Jika memilih 'dalle3', masukkan API Key:
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxx
```

### Mekanisme Keandalan (Reliability Guardrail):
Jika Anda mengaktifkan mode AI (`pollinations` atau `dalle3`) dan server AI mengalami timeout atau kegagalan koneksi, sistem secara otomatis beralih (*graceful fallback*) ke **SVG Template Engine** sehingga proses proposal kampanye **tidak akan pernah gagal atau crash**.

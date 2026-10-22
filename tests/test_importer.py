import os
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.pos_importer import detect_pos_vendor

def test_detect_pos_vendor():
    # Test Moka headers
    moka_headers = ["Receipt Number", "Date", "Time", "Item", "Category", "Quantity", "Gross Sales"]
    assert detect_pos_vendor(moka_headers) == "Moka POS"

    # Test Pawoon headers
    pawoon_headers = ["No Transaksi", "Tanggal", "Jam", "Nama Produk", "Kategori Produk", "Harga Normal"]
    assert detect_pos_vendor(pawoon_headers) == "Pawoon POS"

    # Test Majoo headers
    majoo_headers = ["Nomor Pesanan", "Waktu Transaksi", "Menu", "Qty", "Harga Normal", "Diskon Produk"]
    assert detect_pos_vendor(majoo_headers) == "Majoo POS"

    # Test Generic
    generic_headers = ["order_id", "date", "product_name", "qty", "price", "subtotal"]
    assert detect_pos_vendor(generic_headers) == "Generic Standard POS"

@pytest.mark.asyncio
async def test_sample_template_download():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/pos/sample-templates/moka")
        assert response.status_code == 200
        assert "Receipt Number" in response.text or "Gross Sales" in response.text

@pytest.mark.asyncio
async def test_load_sample_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/v1/pos/load-sample/moka", data={"outlet_id": 1})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "Moka POS" in data["data"]["detected_vendor"]

@pytest.mark.asyncio
async def test_import_jobs_list():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/pos/jobs?outlet_id=1")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "data" in data
        assert isinstance(data["data"], list)

@pytest.mark.asyncio
async def test_dashboard_page():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/juragan")
        assert response.status_code == 200
        assert "JuraganAI" in response.text
        assert "Pilar 1: POS Data Importer" in response.text

        home = await ac.get("/")
        assert home.status_code == 200
        assert "Kirata" in home.text

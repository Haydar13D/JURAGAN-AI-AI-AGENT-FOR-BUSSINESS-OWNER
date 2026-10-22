import os
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, status, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.pos_import import POSImportResponse
from app.services.pos_importer import POSImporterService

router = APIRouter(prefix="/pos", tags=["POS Data Import"])

SYNTHETIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "synthetic"))

VENDOR_FILES = {
    "moka": "moka_pos_sample.csv",
    "pawoon": "pawoon_pos_sample.csv",
    "majoo": "majoo_pos_sample.csv",
    "generic": "pos_transactions_october_2026.csv",
    "excel": "template_input_transaksi_juragan_ai.xlsx",
    "template": "template_input_transaksi_juragan_ai.xlsx"
}

@router.post("/import", response_model=POSImportResponse, summary="Import data penjualan POS (CSV/XLSX)")
async def import_pos_file(
    file: UploadFile = File(..., description="File ekspor transaksi POS (.csv, .xlsx, .xls)"),
    outlet_id: int = Form(1, description="ID Outlet"),
    db: AsyncSession = Depends(get_db)
):
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nama file tidak valid.")

    filename_lower = file.filename.lower()
    if not (filename_lower.endswith(".csv") or filename_lower.endswith(".xlsx") or filename_lower.endswith(".xls")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format file tidak didukung. Harap unggah file .csv atau .xlsx"
        )

    try:
        content = await file.read()
        summary = await POSImporterService.import_file(
            session=db,
            file_bytes=content,
            filename=file.filename,
            outlet_id=outlet_id
        )
        return POSImportResponse(
            status="success",
            message=f"Berhasil mengimpor {summary.imported_transactions} transaksi [{summary.detected_vendor}] dari file {file.filename}",
            data=summary
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan saat memproses file POS: {str(e)}"
        )

@router.get("/jobs", summary="Riwayat Log Import Data POS")
async def list_import_jobs(
    outlet_id: int = Query(1, description="ID Outlet"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    try:
        jobs = await POSImporterService.get_import_jobs(session=db, outlet_id=outlet_id, limit=limit)
        return {
            "status": "success",
            "total_jobs": len(jobs),
            "data": [
                {
                    "job_id": j.id,
                    "filename": j.filename,
                    "file_type": j.file_type,
                    "status": j.status,
                    "total_rows": j.total_rows,
                    "imported_rows": j.imported_rows,
                    "error_message": j.error_message,
                    "started_at": j.started_at,
                    "completed_at": j.completed_at
                }
                for j in jobs
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sample-templates/{vendor}", summary="Download Template Sample Dataset POS")
async def download_sample_template(vendor: str):
    vendor_key = vendor.lower().strip()
    if vendor_key not in VENDOR_FILES:
        raise HTTPException(
            status_code=404,
            detail=f"Vendor '{vendor}' tidak ditemukan. Pilihan: {list(VENDOR_FILES.keys())}"
        )
    file_name = VENDOR_FILES[vendor_key]
    file_path = os.path.join(SYNTHETIC_DIR, file_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File template belum tersedia di server.")
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if file_name.endswith(".xlsx") else "text/csv"
    return FileResponse(path=file_path, filename=file_name, media_type=media_type)

@router.post("/load-sample/{vendor}", response_model=POSImportResponse, summary="One-Click Import Sample POS")
async def load_sample_dataset(
    vendor: str,
    outlet_id: int = Form(1),
    db: AsyncSession = Depends(get_db)
):
    vendor_key = vendor.lower().strip()
    if vendor_key not in VENDOR_FILES:
        raise HTTPException(status_code=404, detail=f"Vendor tidak valid. Pilihan: {list(VENDOR_FILES.keys())}")
    file_path = os.path.join(SYNTHETIC_DIR, VENDOR_FILES[vendor_key])
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File sample belum dibuat di server.")

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    summary = await POSImporterService.import_file(
        session=db,
        file_bytes=file_bytes,
        filename=VENDOR_FILES[vendor_key],
        outlet_id=outlet_id
    )
    return POSImportResponse(
        status="success",
        message=f"Berhasil mengimpor sample dataset [{summary.detected_vendor}] ({summary.imported_transactions} transaksi baru)",
        data=summary
    )

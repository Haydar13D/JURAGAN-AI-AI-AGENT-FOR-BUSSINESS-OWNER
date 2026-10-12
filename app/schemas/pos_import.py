from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

class POSImportSummary(BaseModel):
    job_id: int
    filename: str
    detected_vendor: str = "Generic"
    status: str
    total_rows_read: int
    imported_transactions: int
    imported_items: int
    skipped_rows: int
    earliest_transaction: Optional[datetime] = None
    latest_transaction: Optional[datetime] = None
    total_revenue_imported: float = 0.0

class POSImportResponse(BaseModel):
    status: str = "success"
    message: str
    data: POSImportSummary

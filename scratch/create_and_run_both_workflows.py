import urllib.request
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://localhost:8005/api/v1/workflows"

def post(url, data=None):
    payload = json.dumps(data).encode("utf-8") if data is not None else b"{}"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get(url):
    req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

print("=== 1. MEMBUAT DAN MENJALANKAN KASUS 1: RETAIL IPHONE PRICING ===")
wf1_resp = post(f"{BASE}/from-template", {
    "template_id": "tpl_used_iphone_pricing_v1",
    "tenant_id": "tenant_juragan_retail",
    "name": "Katalog Harga iPhone Toko Utama (Kasus 1)",
    "onboarding": {
        "target_margin_percent": 18.0,
        "fixed_overhead_cost_idr": 75000,
        "rounding_unit_idr": 50000,
        "min_margin_floor_idr": 250000,
        "max_market_price_deviation_pct": 25.0
    }
})
wf1_id = wf1_resp["workflow"]["id"]
print(f"✓ Workflow 1 dibuat: ID={wf1_id}, Nama={wf1_resp['workflow']['name']}")

# Dry run 1
dry1 = post(f"{BASE}/{wf1_id}/dry-run?version=1", {})
print(f"✓ Dry-run Kasus 1: is_clean={dry1['is_clean']}, summary='{dry1['summary']}'")

# Approve Config 1
appr1 = post(f"{BASE}/{wf1_id}/approve?version=1", {})
print(f"✓ Approval Konfigurasi (Tier 1): Status={appr1['status']}, Active={appr1['active_version']}")

# Live Run 1
run1 = post(f"{BASE}/{wf1_id}/runs", {})
run1_id = run1["run_id"]
print(f"✓ Live Run Kasus 1: RunID={run1_id}, Status={run1['status']}")

# Action Approval 1
if run1.get("pending_action"):
    act1_id = run1["pending_action"]["action_id"]
    print(f"  -> Menunggu Action Approval (Tier 2): ActionID={act1_id}")
    dec1 = post(f"{BASE}/runs/{run1_id}/actions/{act1_id}/decision", {
        "decision": "APPROVE",
        "reviewer_user_id": "owner_retail_01",
        "decision_reason": "Margin 18% dan harga pasar tervalidasi aman."
    })
    print(f"✓ Keputusan Aksi Bisnis: {dec1['status']}, Ekspor={dec1['result']['exported_rows_count']} unit siap jual")


print("\n=== 2. MEMBUAT DAN MENJALANKAN KASUS 2: F&B SALES-TO-MARKETING ===")
wf2_resp = post(f"{BASE}/from-template", {
    "template_id": "tpl_fnb_sales_to_marketing_v1",
    "tenant_id": "tenant_resto_sudirman",
    "name": "Otomasi Marketing Resto Sudirman (Kasus 2)",
    "onboarding": {
        "outlet_id": 1,
        "days_lookback": 30,
        "default_discount_pct": 20.0,
        "target_audience": "Pekerja kantor Sudirman & penikmat promo makan siang"
    }
})
wf2_id = wf2_resp["workflow"]["id"]
print(f"✓ Workflow 2 dibuat: ID={wf2_id}, Nama={wf2_resp['workflow']['name']}")

# Dry run 2
dry2 = post(f"{BASE}/{wf2_id}/dry-run?version=1", {})
print(f"✓ Dry-run Kasus 2: is_clean={dry2['is_clean']}, summary='{dry2['summary']}'")

# Approve Config 2
appr2 = post(f"{BASE}/{wf2_id}/approve?version=1", {})
print(f"✓ Approval Konfigurasi (Tier 1): Status={appr2['status']}, Active={appr2['active_version']}")

# Live Run 2
run2 = post(f"{BASE}/{wf2_id}/runs", {})
run2_id = run2["run_id"]
print(f"✓ Live Run Kasus 2: RunID={run2_id}, Status={run2['status']}")

# Action Approval 2
if run2.get("pending_action"):
    act2_id = run2["pending_action"]["action_id"]
    print(f"  -> Menunggu Action Approval (Tier 2): ActionID={act2_id}")
    dec2 = post(f"{BASE}/runs/{run2_id}/actions/{act2_id}/decision", {
        "decision": "APPROVE",
        "reviewer_user_id": "owner_fnb_sudirman",
        "decision_reason": "Materi flyer promo dan hook caption Instagram disetujui."
    })
    print(f"✓ Keputusan Aksi Bisnis: {dec2['status']}, Platform={dec2['result']['platform']}, PostID={dec2['result']['published_post_id']}")

print("\n=== KEDUA WORKFLOW BERHASIL DI-BUILD & DIEKSEKUSI SECARA NYATA ===")

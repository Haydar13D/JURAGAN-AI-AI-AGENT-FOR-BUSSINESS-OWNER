import pytest
from datetime import datetime
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.analytics_engine import parse_date_boundary

def test_parse_date_boundary():
    # Test start date
    start_dt = parse_date_boundary("2026-10-12", is_end=False)
    assert start_dt.year == 2026
    assert start_dt.month == 10
    assert start_dt.day == 12
    assert start_dt.hour == 0
    assert start_dt.minute == 0
    assert start_dt.second == 0

    # Test end date
    end_dt = parse_date_boundary("2026-10-12", is_end=True)
    assert end_dt.year == 2026
    assert end_dt.month == 10
    assert end_dt.day == 12
    assert end_dt.hour == 23
    assert end_dt.minute == 59
    assert end_dt.second == 59

@pytest.mark.asyncio
async def test_daily_trend_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get(
            "/api/v1/analytics/daily-trend",
            params={"outlet_id": 1, "start_date": "2026-10-01", "end_date": "2026-10-12"}
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        if len(data) > 0:
            assert "gmv" in data[0]
            assert "day_name" in data[0]

@pytest.mark.asyncio
async def test_category_breakdown_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get(
            "/api/v1/analytics/category-breakdown",
            params={"outlet_id": 1, "start_date": "2026-10-01", "end_date": "2026-10-12"}
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        if len(data) > 0:
            assert "category_name" in data[0]
            assert "total_revenue" in data[0]

@pytest.mark.asyncio
async def test_day_of_week_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get(
            "/api/v1/analytics/day-of-week",
            params={"outlet_id": 1, "start_date": "2026-10-01", "end_date": "2026-10-12"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "weekday_avg_daily_gmv" in data
        assert "weekend_avg_daily_gmv" in data
        assert "weekend_lift_percentage" in data

@pytest.mark.asyncio
async def test_basket_size_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get(
            "/api/v1/analytics/basket-size",
            params={"outlet_id": 1, "start_date": "2026-10-01", "end_date": "2026-10-12"}
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        if len(data) > 0:
            assert "basket_size_label" in data[0]
            assert "order_count" in data[0]

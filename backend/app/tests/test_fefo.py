from app.engines.fefo import consume_fefo, expire_lots, sort_lots_fefo

def test_fefo_order():
    lots = [
        {"id": 2, "qty_remain": 3, "expiry": "2026-02-01"},
        {"id": 1, "qty_remain": 2, "expiry": "2026-01-10"},
    ]
    assert [l["id"] for l in sort_lots_fefo(lots)] == [1, 2]
    r = consume_fefo(lots, 3)
    assert r["ok"] and r["deductions"][0]["lot_id"] == 1 and r["deductions"][0]["take"] == 2
    assert r["deductions"][1]["take"] == 1

def test_short():
    r = consume_fefo([{"id": 1, "qty_remain": 1, "expiry": "2026-01-01"}], 5)
    assert r["ok"] is False and r["short"] == 4

def test_expire():
    ids = expire_lots([
        {"id": 1, "qty_remain": 1, "expiry": "2025-01-01"},
        {"id": 2, "qty_remain": 1, "expiry": "2027-01-01"},
    ], "2026-01-01")
    assert ids == [1]

def test_fefo_order_follows_override():
    # lot1 钉 60 天（生效 2026-12-04），lot2 日历 2026-09-28 → lot2 先扣
    lots = [
        {"id": 1, "qty_remain": 2, "expiry": "2026-10-01",
         "override_days": 60, "override_set_at": "2026-10-05"},
        {"id": 2, "qty_remain": 1, "expiry": "2026-09-28"},
    ]
    assert [l["id"] for l in sort_lots_fefo(lots)] == [2, 1]
    r = consume_fefo(lots, 2)
    assert r["ok"] and r["deductions"][0]["lot_id"] == 2

def test_expire_respects_override():
    lots = [
        # 日历已过期但钉了 30 天（生效 2026-11-04）→ 留架
        {"id": 1, "qty_remain": 1, "expiry": "2025-01-01",
         "override_days": 30, "override_set_at": "2026-10-05"},
        # 日历未到期但钉值已失效（生效 2026-09-04）→ 下架
        {"id": 2, "qty_remain": 1, "expiry": "2027-01-01",
         "override_days": 3, "override_set_at": "2026-09-01"},
        # 无钉日历过期 → 下架
        {"id": 3, "qty_remain": 1, "expiry": "2025-06-01"},
        # 无钉未到期 → 留架
        {"id": 4, "qty_remain": 1, "expiry": "2027-01-01"},
    ]
    assert expire_lots(lots, "2026-10-05") == [2, 3]

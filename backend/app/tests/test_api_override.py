"""手钉可放天数 API 一致性测试:钉写 / 顶条 / 详情 / 下架 / 消费 / warn_days 变基。

每个用例独立临时 DATA_DIR,seed 重新初始化;日期全部相对真实今天计算,与运行日无关。
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import seed


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    with TestClient(app) as c:
        yield c


def iso(d):
    return d.isoformat()


def new_lot(client, item_id=1, qty=1, expiry=None):
    return client.post("/api/lots", json={
        "item_id": item_id, "qty": qty, "expiry": expiry or iso(date.today()),
    }).json()["id"]


def alert_map(client):
    return {a["id"]: a for a in client.get("/api/alerts").json()}


def effective(lot_row):
    if lot_row["override_days"] is not None:
        return date.fromisoformat(lot_row["override_set_on"]) + timedelta(days=lot_row["override_days"])
    return date.fromisoformat(lot_row["expiry"])


def consumable_total(client, item_id):
    today = date.today()
    return sum(
        l["qty_remain"] for l in client.get("/api/fridge").json()
        if l["item_id"] == item_id and effective(l) > today
    )


def test_pin_expired_lot_unifies_alert_detail_sweep_fridge(client):
    today = date.today()
    yesterday = iso(today - timedelta(days=1))
    lid = new_lot(client, qty=1, expiry=yesterday)
    assert alert_map(client)[lid]["level"] == "expired"  # 钉前:日历已过期

    d = client.put(f"/api/lots/{lid}/override", json={"override_days": 3}).json()
    assert d["override_days"] == 3 and d["override_set_on"] == iso(today)
    assert d["effective_expiry"] == iso(today + timedelta(days=3))
    assert d["level"] == "soon" and d["days_left"] == 3
    assert d["expiry"] == yesterday  # 入库到期日原样保留

    # 顶条与详情对同一批同一紧急结论
    a = alert_map(client)[lid]
    assert a["level"] == d["level"] == "soon" and a["days_left"] == d["days_left"] == 3

    # 过期下架不删它;全层仍在架且主行仍显示入库到期日
    assert lid not in client.post("/api/expire-sweep").json()["expired_ids"]
    row = {l["id"]: l for l in client.get("/api/fridge").json()}[lid]
    assert row["expiry"] == yesterday and row["override_days"] == 3


def test_non_positive_override_rejected_and_state_unchanged(client):
    lid = new_lot(client, qty=1, expiry=iso(date.today() - timedelta(days=1)))
    before = client.get(f"/api/lots/{lid}").json()
    for bad in (0, -1, -100):
        r = client.put(f"/api/lots/{lid}/override", json={"override_days": bad})
        assert r.status_code == 400
    after = client.get(f"/api/lots/{lid}").json()
    # 拒绝后各入口停在改前
    assert after["override_days"] is None and after["level"] == before["level"] == "expired"
    assert alert_map(client)[lid]["level"] == "expired"
    assert lid in client.post("/api/expire-sweep").json()["expired_ids"]


def test_consume_and_alert_share_override_basis(client):
    today = date.today()
    yesterday = iso(today - timedelta(days=1))
    expired_lot = new_lot(client, qty=7, expiry=yesterday)          # 日历过期未钉
    pinned_lot = new_lot(client, qty=6, expiry=yesterday)           # 日历过期但钉 5 天
    client.put(f"/api/lots/{pinned_lot}/override", json={"override_days": 5})

    total = consumable_total(client, 1)
    r = client.post("/api/consume", json={"item_id": 1, "qty": total})
    assert r.status_code == 200  # 钉批计入候选,总量刚好够
    after = {l["id"]: l for l in client.get("/api/fridge").json()}
    assert pinned_lot not in after                       # 钉批被扣空下架
    assert after[expired_lot]["qty_remain"] == 7         # 未钉过期批一分未扣
    assert client.post("/api/consume", json={"item_id": 1, "qty": 1}).status_code == 409


def test_warn_days_rebase_keeps_single_basis(client):
    today = date.today()
    lid = new_lot(client, qty=2, expiry=iso(today - timedelta(days=1)))
    client.put(f"/api/lots/{lid}/override", json={"override_days": 3})  # 有效剩 3 天

    assert alert_map(client)[lid]["level"] == "soon"     # warn=3: 紧急
    assert client.put("/api/settings", json={"warn_days": 1}).status_code == 200
    assert lid not in alert_map(client)                  # 新 warn=1: 顶条不再紧急
    d = client.get(f"/api/lots/{lid}").json()
    assert d["level"] is None and d["days_left"] == 3    # 详情与顶条同结论
    # 消费资格只跟 override 基准,不跟新 warn:钉批仍可扣
    assert client.post("/api/consume", json={"item_id": 1, "qty": consumable_total(client, 1)}).status_code == 200
    assert client.put("/api/settings", json={"warn_days": -1}).status_code == 400


def test_unpin_restores_calendar_basis(client):
    lid = new_lot(client, qty=1, expiry=iso(date.today() - timedelta(days=1)))
    client.put(f"/api/lots/{lid}/override", json={"override_days": 3})
    assert lid not in client.post("/api/expire-sweep").json()["expired_ids"]
    d = client.put(f"/api/lots/{lid}/override", json={"override_days": None}).json()
    assert d["override_days"] is None and d["level"] == "expired"
    assert alert_map(client)[lid]["level"] == "expired"
    assert lid in client.post("/api/expire-sweep").json()["expired_ids"]


def test_cannot_pin_lot_not_on_shelf(client):
    lid = new_lot(client, qty=1, expiry=iso(date.today() - timedelta(days=1)))
    client.post("/api/expire-sweep")
    r = client.put(f"/api/lots/{lid}/override", json={"override_days": 3})
    assert r.status_code == 409
    assert client.put("/api/lots/999999/override", json={"override_days": 3}).status_code == 404

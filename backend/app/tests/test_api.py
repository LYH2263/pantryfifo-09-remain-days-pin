"""HTTP 端到端：手钉可放天数各入口一致性（需要 httpx，缺则跳过）。"""
import os
import tempfile
from datetime import date, timedelta

import pytest

httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from app.seed import init_db  # noqa: E402


def iso(offset_days: int) -> str:
    return (date.today() + timedelta(days=offset_days)).isoformat()


@pytest.fixture()
def client():
    os.environ["DATA_DIR"] = tempfile.mkdtemp()
    init_db()
    from app.main import app
    with TestClient(app) as c:
        yield c


def inbound(client, expiry_offset, qty=1, item_id=1):
    r = client.post("/api/lots", json={
        "item_id": item_id, "qty": qty, "expiry": iso(expiry_offset)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def alert_of(client, lot_id):
    rows = client.get("/api/alerts").json()
    return next((r for r in rows if r["id"] == lot_id), None)


def test_override_flow_end_to_end(client):
    lid = inbound(client, -1)  # 昨天到期：日历已过期但在架
    assert alert_of(client, lid)["level"] == "expired"

    r = client.post(f"/api/lots/{lid}/override", json={"override_days": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["override_days"] == 2
    assert body["override_set_at"] == date.today().isoformat()
    assert body["effective_expiry"] == iso(2)
    assert body["level"] == "soon" and body["days_left"] == 2
    assert body["expiry"] == iso(-1)  # 入库到期日不改写

    # 顶条与详情对同一批同一结论
    a = alert_of(client, lid)
    d = client.get(f"/api/lots/{lid}").json()
    assert a["level"] == d["level"] == "soon"
    assert a["days_left"] == d["days_left"] == 2

    # 下架名单不再含它；全层在架仍有它且主行仍是入库到期日
    swept = client.post("/api/expire-sweep").json()["expired_ids"]
    assert lid not in swept
    row = next(x for x in client.get("/api/fridge").json() if x["id"] == lid)
    assert row["expiry"] == iso(-1) and row["override_days"] == 2


def test_override_non_positive_rejected(client):
    lid = inbound(client, -1)
    for bad in (0, -3):
        r = client.post(f"/api/lots/{lid}/override", json={"override_days": bad})
        assert r.status_code == 400 and r.json()["detail"] == "override_days_non_positive"
    r = client.post(f"/api/lots/{lid}/override", json={"override_days": 2.5})
    assert r.status_code == 422
    # 拒绝后各入口停在改前
    d = client.get(f"/api/lots/{lid}").json()
    assert d["override_days"] is None and d["level"] == "expired"
    assert alert_of(client, lid)["level"] == "expired"
    assert lid in client.post("/api/expire-sweep").json()["expired_ids"]


def test_override_requires_on_shelf(client):
    lid = inbound(client, -1)
    client.post("/api/expire-sweep")
    r = client.post(f"/api/lots/{lid}/override", json={"override_days": 5})
    assert r.status_code == 409 and r.json()["detail"] == "not_on_shelf"
    r = client.post("/api/lots/999/override", json={"override_days": 5})
    assert r.status_code == 404 and r.json()["detail"] == "lot_not_found"


def test_warn_rebase_then_alert_detail_consume_follow_override(client):
    near = inbound(client, 1)   # 明天到期
    far = inbound(client, 2)    # 后天到期
    # 给更近的批钉 10 天 → 生效今天+10，FEFO 应先扣 far
    r = client.post(f"/api/lots/{near}/override", json={"override_days": 10})
    assert r.status_code == 200
    # warn_days 变基 3 → 30：顶条与详情同跟 override
    assert client.post("/api/settings", json={"warn_days": 30}).status_code == 200
    a = alert_of(client, near)
    d = client.get(f"/api/lots/{near}").json()
    assert a["level"] == d["level"] == "soon" and a["days_left"] == d["days_left"] == 10
    # 消费扣到谁与顶条同一基准：钉过的 near 排后，先扣 far（按相对顺序断言，与种子批无关）
    r = client.post("/api/consume", json={"item_id": 1, "qty": 5})
    assert r.status_code == 200
    fefo = [x["lot_id"] for x in r.json()["deductions"] if x["lot_id"] in (near, far)]
    assert fefo == [far, near]


def test_clear_override(client):
    lid = inbound(client, -1)
    client.post(f"/api/lots/{lid}/override", json={"override_days": 5})
    assert alert_of(client, lid) is None  # 生效今天+5，warn=3 → 不紧急
    r = client.post(f"/api/lots/{lid}/override", json={"override_days": None})
    assert r.status_code == 200
    assert r.json()["override_days"] is None
    assert alert_of(client, lid)["level"] == "expired"


def test_settings_negative_rejected(client):
    r = client.post("/api/settings", json={"warn_days": -1})
    assert r.status_code == 400 and r.json()["detail"] == "warn_days_negative"

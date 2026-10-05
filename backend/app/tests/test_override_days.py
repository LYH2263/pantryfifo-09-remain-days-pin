"""手钉可放天数：规则一致性测试（只依赖 stdlib，可直接用 python3 跑）。"""
import os
import tempfile
import threading
from datetime import date

from app.db import connect, write_tx
from app.engines.fefo import consume_fefo, expire_lots, sort_lots_fefo
from app.modules.override_days import (
    OverrideError, alert_rows, effective_expiry, lot_detail, lot_level,
    pin_override, validate_override_days,
)
from app.seed import init_db

TODAY = date(2026, 10, 5)


def fresh_db():
    os.environ["DATA_DIR"] = tempfile.mkdtemp()
    init_db()
    return connect()


def expect_err(reason, fn, *args):
    try:
        fn(*args)
    except OverrideError as e:
        assert e.reason == reason, f"want {reason}, got {e.reason}"
        return
    raise AssertionError(f"expected OverrideError({reason})")


def shelf_lots(c):
    return [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]


# ---------- 纯规则 ----------

def test_validate_rejects_non_positive():
    for bad in (0, -1, -100, True, 2.5, "3"):
        expect_err("override_days_non_positive", validate_override_days, bad)
    assert validate_override_days(1) == 1
    assert validate_override_days(365) == 365


def test_effective_expiry_pure():
    assert effective_expiry({"expiry": "2026-01-10"}) == "2026-01-10"
    # 有钉值但缺写入日 → 回落日历到期日
    assert effective_expiry({"expiry": "2026-01-10", "override_days": 5}) == "2026-01-10"
    assert effective_expiry(
        {"expiry": "2026-01-10", "override_days": 5, "override_set_at": "2026-10-05"}
    ) == "2026-10-10"
    assert effective_expiry({}) is None


def test_lot_level_boundaries():
    assert lot_level({"expiry": "2026-10-04"}, 3, TODAY)["level"] == "expired"
    soon0 = lot_level({"expiry": "2026-10-05"}, 3, TODAY)
    assert soon0["level"] == "soon" and soon0["days_left"] == 0
    assert lot_level({"expiry": "2026-10-08"}, 3, TODAY)["level"] == "soon"  # == warn
    assert lot_level({"expiry": "2026-10-09"}, 3, TODAY)["level"] == "ok"
    none = lot_level({}, 3, TODAY)
    assert none["level"] == "ok" and none["days_left"] is None


# ---------- 库内流程 ----------

def test_pin_expired_lot_unifies_sweep_alert_shelf():
    """日历已过期批允许钉：钉后下架名单 / 顶条 expired / 在架同一资格。"""
    c = fresh_db()
    # lot 1: 牛奶 expiry 2026-10-01，2026-10-05 已日历过期
    assert any(r["id"] == 1 and r["level"] == "expired" for r in alert_rows(c, TODAY))
    assert 1 in expire_lots(shelf_lots(c), TODAY.isoformat())

    d = pin_override(c, 1, 2, TODAY)
    assert d["override_days"] == 2 and d["override_set_at"] == "2026-10-05"
    assert d["effective_expiry"] == "2026-10-07"
    assert d["level"] == "soon" and d["days_left"] == 2  # warn_days=3
    assert d["expiry"] == "2026-10-01"  # 入库到期日不改写

    # 顶条：expired → soon，与详情同一结论
    a1 = [r for r in alert_rows(c, TODAY) if r["id"] == 1]
    assert len(a1) == 1 and a1[0]["level"] == "soon" and a1[0]["days_left"] == 2
    # 下架名单不再含它
    assert 1 not in expire_lots(shelf_lots(c), TODAY.isoformat())
    # 仍在架
    assert c.execute("SELECT status FROM lots WHERE id=1").fetchone()["status"] == "on_shelf"
    c.close()


def test_pin_non_positive_rejected_and_state_unchanged():
    """非正数拒写；拒绝后顶条 / 下架名单 / 在架都停在改前。"""
    c = fresh_db()
    expect_err("override_days_non_positive", pin_override, c, 1, 0, TODAY)
    expect_err("override_days_non_positive", pin_override, c, 1, -7, TODAY)
    row = dict(c.execute("SELECT * FROM lots WHERE id=1").fetchone())
    assert row["override_days"] is None and row["override_set_at"] is None
    assert any(r["id"] == 1 and r["level"] == "expired" for r in alert_rows(c, TODAY))
    assert 1 in expire_lots(shelf_lots(c), TODAY.isoformat())
    c.close()


def test_pin_requires_on_shelf():
    """仅在架批可写：不存在 404 级、已下架 409 级。"""
    c = fresh_db()
    expect_err("lot_not_found", pin_override, c, 999, 5, TODAY)
    # lot 4（冻饺 2025-01-01）先被过期下架 → 离架批拒写
    ids = expire_lots(shelf_lots(c), TODAY.isoformat())
    assert 4 in ids
    for i in ids:
        c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
    c.commit()
    expect_err("not_on_shelf", pin_override, c, 4, 30, TODAY)
    c.close()


def test_warn_rebase_and_consume_follow_override():
    """warn_days 变基后：顶条与详情同跟 override；消费顺序也跟 override。"""
    c = fresh_db()
    # lot 2（牛奶 2026-09-28）钉 60 天 → 生效 2026-12-04
    pin_override(c, 2, 60, TODAY)
    assert all(r["id"] != 2 for r in alert_rows(c, TODAY))  # warn=3 时不紧急
    # warn_days 变基 3 → 90
    c.execute("UPDATE settings SET value='90' WHERE key='warn_days'")
    c.commit()
    a = [r for r in alert_rows(c, TODAY) if r["id"] == 2]
    d = lot_detail(c, 2, TODAY)
    assert a and a[0]["level"] == d["level"] == "soon"
    assert a[0]["days_left"] == d["days_left"] == 60
    # 消费扣减顺序跟 override：lot1(2026-10-01) 先，lot2(生效 2026-12-04) 后
    lots = [dict(r) for r in c.execute(
        "SELECT * FROM lots WHERE item_id=1 AND status='on_shelf' AND qty_remain>0")]
    assert [l["id"] for l in sort_lots_fefo(lots)] == [1, 2]
    r = consume_fefo(lots, 2)
    assert r["ok"] and r["deductions"][0]["lot_id"] == 1
    c.close()


def test_clear_override_restores_calendar_rule():
    """钉值可清除（None），清除后回到日历规则。"""
    c = fresh_db()
    pin_override(c, 1, 10, TODAY)
    d = pin_override(c, 1, None, TODAY)
    assert d["override_days"] is None and d["override_set_at"] is None
    assert d["effective_expiry"] == "2026-10-01" and d["level"] == "expired"
    assert any(r["id"] == 1 and r["level"] == "expired" for r in alert_rows(c, TODAY))
    assert 1 in expire_lots(shelf_lots(c), TODAY.isoformat())
    c.close()


def test_detail_and_alert_same_conclusion_for_all_lots():
    """顶条与详情对每一批得出同一紧急结论。"""
    c = fresh_db()
    pin_override(c, 1, 2, TODAY)   # 生效 2026-10-07 → soon
    pin_override(c, 3, 1, TODAY)   # 鸡蛋 2026-11-01 → 生效 2026-10-06 → soon
    alerts = {r["id"]: r for r in alert_rows(c, TODAY)}
    for lid in (1, 2, 3, 4, 5):
        d = lot_detail(c, lid, TODAY)
        if lid in alerts:
            assert alerts[lid]["level"] == d["level"]
            assert alerts[lid]["effective_expiry"] == d["effective_expiry"]
        else:
            assert d["level"] == "ok" or d["qty_remain"] <= 0
    c.close()


def test_concurrent_pin_settings_sweep_stay_consistent():
    """钉值写入 / 改 warn_days / 过期下架并发：无锁错误，收敛后各视图同一资格。"""
    c0 = fresh_db()
    c0.close()
    errors = []

    def hammer_override():
        for i in range(20):
            c = connect()
            try:
                pin_override(c, 1, (i % 5) + 1, TODAY)  # lot 1 日历已过期但在架
            except OverrideError:
                pass  # 与下架交错时合法拒写
            except Exception as e:
                errors.append(("override", repr(e)))
            finally:
                c.close()

    def hammer_settings():
        for i in range(20):
            c = connect()
            try:
                with write_tx(c):
                    c.execute(
                        "INSERT INTO settings(key,value) VALUES ('warn_days',?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (str(i % 10),))
            except Exception as e:
                errors.append(("settings", repr(e)))
            finally:
                c.close()

    def hammer_sweep():
        for _ in range(20):
            c = connect()
            try:
                with write_tx(c):
                    lots = [dict(r) for r in c.execute(
                        "SELECT * FROM lots WHERE status='on_shelf'")]
                    for i in expire_lots(lots, TODAY.isoformat()):
                        c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
            except Exception as e:
                errors.append(("sweep", repr(e)))
            finally:
                c.close()

    threads = [threading.Thread(target=f)
               for f in (hammer_override, hammer_settings, hammer_sweep)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors

    # 收敛后：顶条与详情同结论；下架名单 == 顶条 expired 集合
    c = connect()
    alerts = {r["id"]: r for r in alert_rows(c, TODAY)}
    for lid in [r["id"] for r in c.execute("SELECT id FROM lots")]:
        d = lot_detail(c, lid, TODAY)
        if lid in alerts:
            assert alerts[lid]["level"] == d["level"]
    sweep_ids = set(expire_lots(shelf_lots(c), TODAY.isoformat()))
    bar_expired = {i for i, r in alerts.items() if r["level"] == "expired"}
    assert sweep_ids == bar_expired
    c.close()


def test_existing_db_migrates():
    """旧库（无钉值列）init_db 后补列且数据不丢。"""
    os.environ["DATA_DIR"] = tempfile.mkdtemp()
    c = connect()
    c.executescript("""
    CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT, layer TEXT, unit TEXT);
    CREATE TABLE lots(
      id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, qty_in REAL, qty_remain REAL,
      expiry TEXT, status TEXT, data_quality TEXT
    );
    CREATE TABLE consumptions(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT, result_json TEXT, created_at TEXT);
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
    INSERT INTO items(id,name,layer,unit) VALUES (1,'牛奶','upper','盒');
    INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality)
      VALUES (1,2,2,'2026-10-01','on_shelf','clean');
    INSERT INTO settings(key,value) VALUES ('warn_days','3');
    """)
    c.commit(); c.close()
    init_db()
    c = connect()
    cols = {r["name"] for r in c.execute("PRAGMA table_info(lots)")}
    assert "override_days" in cols and "override_set_at" in cols
    row = dict(c.execute("SELECT * FROM lots WHERE id=1").fetchone())
    assert row["expiry"] == "2026-10-01" and row["override_days"] is None
    # 迁移后可直接钉
    d = pin_override(c, 1, 5, TODAY)
    assert d["effective_expiry"] == "2026-10-10"
    c.close()

"""手钉可放天数:统一有效到期基准的一致性测试。

场景锚点 today=2026-10-05, warn_days=3。
"""
import pytest
from app.engines.expiry import (
    consumable, days_left, effective_expiry, is_expired, sweep_ids,
    urgency, validate_override_days,
)

TODAY = "2026-10-05"
WARN = 3


def pinned(**kw):
    """日历已过期(10-01)但钉了 2 天(10-05 起算)的在架批。"""
    base = {"id": 1, "qty_remain": 1, "expiry": "2026-10-01",
            "override_days": 2, "override_set_on": "2026-10-05"}
    base.update(kw)
    return base


def test_effective_expiry_anchored_at_pin_date():
    # 钉住日 + 可放天数,锚定不随今天滑动
    assert effective_expiry(pinned()) == "2026-10-07"
    assert days_left(pinned(), TODAY) == 2


def test_unpinned_falls_back_to_calendar_expiry():
    lot = pinned(override_days=None, override_set_on=None)
    assert effective_expiry(lot) == "2026-10-01"
    assert days_left(lot, TODAY) == -4


def test_expired_calendar_lot_pinned_same_eligibility_everywhere():
    """日历已过期但钉了 override:下架名单、顶条 expired、消费资格同一口径。"""
    lot = pinned()
    assert not is_expired(lot, TODAY)              # 顶条不再 expired
    assert sweep_ids([lot], TODAY) == []           # 下架名单不删它
    assert consumable(lot, TODAY)                  # 消费能扣到它
    assert urgency(lot, TODAY, WARN)["level"] == "soon"  # 剩 2 天 <= warn 3


def test_unpinned_expired_lot_is_expired_everywhere():
    lot = pinned(override_days=None, override_set_on=None)
    assert is_expired(lot, TODAY)
    assert sweep_ids([lot], TODAY) == [1]
    assert not consumable(lot, TODAY)
    assert urgency(lot, TODAY, WARN)["level"] == "expired"


def test_expiry_day_itself_is_expired_consistent_with_alert_bar():
    # 到期日当天 days_left=0: 顶条 expired 与下架名单同资格
    lot = pinned(expiry="2026-10-05", override_days=None, override_set_on=None)
    assert is_expired(lot, TODAY)
    assert sweep_ids([lot], TODAY) == [1]


def test_pin_itself_ages_out():
    # 钉 1 天自 10-03 起算 -> 10-04 到期,今天已滑过
    lot = pinned(override_days=1, override_set_on="2026-10-03")
    assert is_expired(lot, TODAY)
    assert sweep_ids([lot], TODAY) == [1]
    assert not consumable(lot, TODAY)


def test_warn_days_rebase_keeps_single_basis():
    """warn_days 变基:已钉批消费资格与顶条紧急都只看 override 基准,不一个跟 override 一个跟新 warn。"""
    lot = pinned()  # 有效剩 2 天
    assert urgency(lot, TODAY, 3)["level"] == "soon"   # 旧 warn=3: 紧急
    assert urgency(lot, TODAY, 1)["level"] is None     # 新 warn=1: 不再紧急
    assert consumable(lot, TODAY)                      # 消费资格不随 warn 变
    assert not is_expired(lot, TODAY)


def test_no_expiry_basis_is_never_urgent_nor_swept():
    lot = {"id": 9, "qty_remain": 1, "expiry": None,
           "override_days": None, "override_set_on": None}
    assert urgency(lot, TODAY, WARN)["level"] is None
    assert sweep_ids([lot], TODAY) == []
    assert consumable(lot, TODAY)


def test_validate_override_days_rejects_non_positive():
    with pytest.raises(ValueError):
        validate_override_days(0)
    with pytest.raises(ValueError):
        validate_override_days(-3)
    with pytest.raises(ValueError):
        validate_override_days(True)
    assert validate_override_days(5) == 5

"""手钉可放天数:单一「有效到期日」基准。

已钉批: 有效到期日 = 钉住日(override_set_on) + override_days,锚定不随今天滑动。
未钉批: 有效到期日 = 入库时的日历 expiry。

顶条 alerts、批次详情、过期下架名单、消费资格必须共用本模块的谓词,
保证对同一批得出同一结论(钉值写入、warn_days 变基、过期下架互不同时各算各的)。
"""
from datetime import date, timedelta


def effective_expiry(lot: dict) -> str | None:
    """有效到期日 ISO 字符串;无日历到期日且未钉时返回 None。"""
    od = lot.get("override_days")
    oso = lot.get("override_set_on")
    if od is not None and oso:
        return (date.fromisoformat(oso) + timedelta(days=int(od))).isoformat()
    return lot.get("expiry") or None


def days_left(lot: dict, today: str) -> int | None:
    """按有效到期日距 today 的天数(可为负);无到期依据返回 None。"""
    eff = effective_expiry(lot)
    if eff is None:
        return None
    return (date.fromisoformat(eff) - date.fromisoformat(today)).days


def level_for(dl: int | None, warn_days: int) -> str | None:
    """紧急级别: expired(<=0,含到期日当天) / soon(<=warn_days) / None。"""
    if dl is None:
        return None
    if dl <= 0:
        return "expired"
    if dl <= warn_days:
        return "soon"
    return None


def urgency(lot: dict, today: str, warn_days: int) -> dict:
    """顶条与批次详情共用的紧急结论。"""
    dl = days_left(lot, today)
    return {
        "effective_expiry": effective_expiry(lot),
        "days_left": dl,
        "level": level_for(dl, warn_days),
    }


def is_expired(lot: dict, today: str) -> bool:
    """过期谓词(有效到期 days_left<=0)。顶条 expired、下架名单、消费门槛同一资格。"""
    dl = days_left(lot, today)
    return dl is not None and dl <= 0


def consumable(lot: dict, today: str) -> bool:
    """消费资格: 有余量且按统一基准未过期。已钉批只看 override 基准,与 warn_days 无关。"""
    return float(lot.get("qty_remain", 0)) > 0 and not is_expired(lot, today)


def sweep_ids(lots: list[dict], today: str) -> list[int]:
    """下架名单: 与顶条 expired 同一谓词。已钉且 override 仍有效的批不下架。"""
    return [
        l["id"]
        for l in lots
        if float(l.get("qty_remain", 0)) > 0 and is_expired(l, today)
    ]


def validate_override_days(v) -> int:
    """钉值校验: 必须为正整数,非正数拒写。返回校验后的天数。"""
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError("override_days_not_int")
    if v <= 0:
        raise ValueError("override_days_non_positive")
    return v

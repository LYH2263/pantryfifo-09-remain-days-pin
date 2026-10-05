import json
from datetime import date, datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect, tx
from app.engines.expiry import consumable, sweep_ids, urgency, validate_override_days
from app.engines.fefo import consume_fefo, expire_lots

app = FastAPI(title="Pantryfifo", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/fridge")
def fridge(layer: str | None = None):
    c = connect()
    q = """SELECT lots.*, items.name, items.layer, items.unit FROM lots
           JOIN items ON items.id=lots.item_id WHERE lots.status='on_shelf'"""
    args = []
    if layer:
        q += " AND items.layer=?"; args.append(layer)
    rows = [dict(r) for r in c.execute(q, args)]; c.close(); return rows

def _warn_days(c) -> int:
    return int(c.execute("SELECT value FROM settings WHERE key='warn_days'").fetchone()["value"])

def _lot_detail(c, lot_id: int, warn: int | None = None) -> dict | None:
    """批次详情: 入库 expiry 原样返回,紧急结论与顶条同一函数同一 warn 基准。"""
    r = c.execute(
        """SELECT lots.*, items.name, items.layer, items.unit FROM lots
           JOIN items ON items.id=lots.item_id WHERE lots.id=?""", (lot_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    if warn is None:
        warn = _warn_days(c)
    d.update(urgency(d, date.today().isoformat(), warn))
    return d

@app.get("/api/alerts")
def alerts():
    today = date.today().isoformat()
    # 一致性读快照: warn_days 与 lots 不被并发写夹在中间
    with tx() as c:
        warn = _warn_days(c)
        rows = [dict(r) for r in c.execute(
            """SELECT lots.*, items.name, items.layer FROM lots JOIN items ON items.id=lots.item_id
               WHERE status='on_shelf' AND qty_remain>0""")]
    out = []
    for r in rows:
        u = urgency(r, today, warn)
        if u["level"]:
            r.update(u)
            out.append(r)
    return out

class LotIn(BaseModel):
    item_id: int
    qty: float
    expiry: str

@app.post("/api/lots")
def inbound(body: LotIn):
    c = connect()
    item = c.execute("SELECT id FROM items WHERE id=?", (body.item_id,)).fetchone()
    if not item: c.close(); raise HTTPException(404, "item")
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
        (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean"))
    c.commit(); lid = cur.lastrowid; c.close(); return {"id": lid}

@app.get("/api/lots/{lot_id}")
def lot_detail(lot_id: int):
    with tx() as c:
        d = _lot_detail(c, lot_id)
    if not d:
        raise HTTPException(404, "lot")
    return d

class OverrideIn(BaseModel):
    override_days: int | None  # 正整数=钉可放天数; null=取消钉

@app.put("/api/lots/{lot_id}/override")
def pin_override(lot_id: int, body: OverrideIn):
    # 非正数拒写(在开启事务前校验,拒绝后各入口停在改前)
    if body.override_days is None:
        days = None
    else:
        try:
            days = validate_override_days(body.override_days)
        except ValueError as e:
            raise HTTPException(400, str(e))
    today = date.today().isoformat()
    with tx(immediate=True) as c:  # 与改 warn_days、过期下架互斥
        row = c.execute("SELECT status FROM lots WHERE id=?", (lot_id,)).fetchone()
        if not row:
            raise HTTPException(404, "lot")
        if row["status"] != "on_shelf":
            raise HTTPException(409, "lot_not_on_shelf")
        if days is None:
            c.execute("UPDATE lots SET override_days=NULL, override_set_on=NULL WHERE id=?", (lot_id,))
        else:
            c.execute("UPDATE lots SET override_days=?, override_set_on=? WHERE id=?",
                      (days, today, lot_id))
        return _lot_detail(c, lot_id)

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    note: str = ""

@app.post("/api/consume")
def consume(body: ConsumeIn):
    today = date.today().isoformat()
    with tx(immediate=True) as c:
        lots = [dict(r) for r in c.execute(
            "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0", (body.item_id,))]
        # 消费资格与顶条 expired 同一基准: 有效过期批不可扣;已钉批只跟 override,不跟新 warn
        lots = [l for l in lots if consumable(l, today)]
        result = consume_fefo(lots, body.qty)
        if not result["ok"] and result["reason"] == "qty_non_positive":
            raise HTTPException(400, result["reason"])
        if not result["ok"]:
            raise HTTPException(409, result)
        for d in result["deductions"]:
            c.execute("UPDATE lots SET qty_remain = qty_remain - ? WHERE id=?", (d["take"], d["lot_id"]))
            rem = c.execute("SELECT qty_remain FROM lots WHERE id=?", (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= 0:
                c.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?", (d["lot_id"],))
        c.execute("INSERT INTO consumptions(note,result_json,created_at) VALUES (?,?,?)",
                  (body.note, json.dumps(result), datetime.now(timezone.utc).isoformat()))
        return result

@app.post("/api/expire-sweep")
def expire_sweep():
    today = date.today().isoformat()
    with tx(immediate=True) as c:
        lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
        ids = expire_lots(lots, today)  # 与顶条 expired 同一谓词: 已钉有效批不下架
        for i in ids:
            c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
    return {"expired_ids": ids}

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows

class SettingsIn(BaseModel):
    warn_days: int

@app.put("/api/settings")
def put_settings(body: SettingsIn):
    if body.warn_days < 0:
        raise HTTPException(400, "warn_days_negative")
    with tx(immediate=True) as c:  # 与钉值写入、过期下架互斥
        c.execute(
            "INSERT INTO settings(key,value) VALUES('warn_days',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(body.warn_days),))
    return {"warn_days": body.warn_days}

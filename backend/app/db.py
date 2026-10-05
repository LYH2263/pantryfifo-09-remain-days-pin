import os, sqlite3
from contextlib import contextmanager
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "pantryfifo.db"

def connect():
    c = sqlite3.connect(db_path())
    c.row_factory = sqlite3.Row
    return c

@contextmanager
def tx(immediate: bool = False):
    """事务作用域。immediate=True 用 BEGIN IMMEDIATE 先取写锁:
    钉值写入 / 改 warn_days / 过期下架同时发生时互斥串行,拒绝路径回滚、各入口停在改前。
    immediate=False 为一致性读快照(如 alerts 同时读 settings 与 lots)。"""
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

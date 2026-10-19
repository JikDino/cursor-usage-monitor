"""SQLite 매매일지. AI의 판단 근거와 결과를 남기고, 성과 요약을 다시 AI에 피드백(reflection)한다."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    ts TEXT, code TEXT, action TEXT, confidence REAL, weight REAL, reason TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS market_views (ts TEXT, source TEXT, view TEXT);
CREATE TABLE IF NOT EXISTS orders (
    ts TEXT, side TEXT, code TEXT, qty INTEGER, price REAL, ok INTEGER, reason TEXT, order_no TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS equity (ts TEXT, equity REAL, cash REAL, positions INTEGER);
CREATE TABLE IF NOT EXISTS position_meta (
    code TEXT PRIMARY KEY, name TEXT, entry_price REAL, peak_price REAL,
    stop_loss_pct REAL, take_profit_pct REAL, opened_at TEXT, entry_reason TEXT
);
CREATE TABLE IF NOT EXISTS closed_trades (
    code TEXT, name TEXT, entry_price REAL, exit_price REAL, qty INTEGER, pnl_pct REAL,
    entry_reason TEXT, exit_reason TEXT, opened_at TEXT, closed_at TEXT
);
"""


def _ts(now: datetime | None) -> str:
    return (now or datetime.now()).isoformat(timespec="seconds")


class Journal:
    def __init__(self, path: str | Path = "data/trader.db"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()) -> None:
        self.db.execute(sql, args)
        self.db.commit()

    # ------------------------------------------------------------ records
    def record_equity(self, now, equity: float, cash: float, positions: int) -> None:
        self._exec("INSERT INTO equity VALUES (?,?,?,?)", (_ts(now), equity, cash, positions))

    def record_analysis(self, now, result) -> None:
        ts = _ts(now)
        self.db.execute("INSERT INTO market_views VALUES (?,?,?)", (ts, result.source, result.market_view))
        self.db.executemany(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
            [(ts, d.code, d.action, d.confidence, d.weight, d.reason, result.source) for d in result.decisions],
        )
        self.db.commit()

    def record_order(self, now, res, reason: str) -> None:
        self._exec(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
            (_ts(now), res.side, res.code, res.qty, res.price, int(res.ok), reason, res.order_no, res.message),
        )

    # ------------------------------------------------------- position meta
    def get_meta(self) -> dict[str, dict]:
        return {r["code"]: dict(r) for r in self.db.execute("SELECT * FROM position_meta")}

    def open_position(self, now, code, name, entry_price, stop_loss_pct, take_profit_pct, reason) -> None:
        self._exec(
            "INSERT OR REPLACE INTO position_meta VALUES (?,?,?,?,?,?,?,?)",
            (code, name, entry_price, entry_price, stop_loss_pct, take_profit_pct, _ts(now), reason),
        )

    def update_peak(self, code: str, price: float) -> None:
        self._exec("UPDATE position_meta SET peak_price = MAX(peak_price, ?) WHERE code = ?", (price, code))

    def close_position(self, now, code: str, exit_price: float, qty: int, reason: str, fallback_entry: float = 0) -> None:
        meta = self.get_meta().get(code, {})
        entry = meta.get("entry_price") or fallback_entry
        pnl = exit_price / entry - 1 if entry else 0.0
        self.db.execute(
            "INSERT INTO closed_trades VALUES (?,?,?,?,?,?,?,?,?,?)",
            (code, meta.get("name", code), entry, exit_price, qty, pnl, meta.get("entry_reason", ""), reason,
             meta.get("opened_at", ""), _ts(now)),
        )
        self.db.execute("DELETE FROM position_meta WHERE code = ?", (code,))
        self.db.commit()

    def drop_meta(self, code: str) -> None:
        self._exec("DELETE FROM position_meta WHERE code = ?", (code,))

    # --------------------------------------------------------------- stats
    def reference_equity(self, now: datetime) -> float | None:
        """일일 손실 한도 기준: 오늘 이전 마지막 평가금액(없으면 오늘 첫 기록)."""
        day = (now or datetime.now()).date().isoformat()
        row = self.db.execute("SELECT equity FROM equity WHERE ts < ? ORDER BY ts DESC LIMIT 1", (day,)).fetchone()
        if row is None:
            row = self.db.execute("SELECT equity FROM equity WHERE ts >= ? ORDER BY ts LIMIT 1", (day,)).fetchone()
        return row["equity"] if row else None

    def trades_today(self, now: datetime) -> int:
        day = (now or datetime.now()).date().isoformat()
        row = self.db.execute("SELECT COUNT(*) c FROM orders WHERE ok = 1 AND ts >= ?", (day,)).fetchone()
        return int(row["c"])

    def closed_trades(self, limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM closed_trades ORDER BY closed_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.db.execute(sql)]

    def equity_curve(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM equity ORDER BY ts")]

    def performance_summary(self, recent: int = 10) -> dict:
        trades = self.closed_trades()
        if not trades:
            return {"closed_trades": 0, "note": "아직 청산된 거래 없음"}
        pnls = [t["pnl_pct"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        by_exit: dict[str, list[float]] = {}
        for t in trades:
            key = t["exit_reason"].split(":")[0]
            by_exit.setdefault(key, []).append(t["pnl_pct"])
        return {
            "closed_trades": len(trades),
            "win_rate": round(len(wins) / len(pnls), 3),
            "avg_win_pct": round(sum(wins) / len(wins), 4) if wins else 0,
            "avg_loss_pct": round(sum(losses) / len(losses), 4) if losses else 0,
            "exit_reason_stats": {k: {"n": len(v), "avg_pnl": round(sum(v) / len(v), 4)} for k, v in by_exit.items()},
            "recent_trades": [
                {
                    "code": t["code"],
                    "name": t["name"],
                    "pnl_pct": round(t["pnl_pct"], 4),
                    "entry_reason": (t["entry_reason"] or "")[:120],
                    "exit_reason": t["exit_reason"],
                }
                for t in trades[:recent]
            ],
        }

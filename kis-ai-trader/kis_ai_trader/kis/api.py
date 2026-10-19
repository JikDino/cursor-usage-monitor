"""Typed wrappers for the KIS domestic-stock endpoints this project uses."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd

from .client import KISClient, KISError

log = logging.getLogger(__name__)


def _num(value, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


class KISApi:
    def __init__(self, client: KISClient):
        self.client = client
        self.cfg = client.cfg

    def _tr(self, real: str, paper: str) -> str:
        return paper if self.cfg.is_paper else real

    # ------------------------------------------------------------ 시세
    def get_price(self, code: str) -> dict:
        data, _ = self.client.request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        o = data["output"]
        return {
            "code": code,
            "price": _num(o.get("stck_prpr")),
            "change_pct": _num(o.get("prdy_ctrt")),
            "volume": _num(o.get("acml_vol")),
            "per": _num(o.get("per")),
            "pbr": _num(o.get("pbr")),
            "market_cap_eok": _num(o.get("hts_avls")),
            "sector": o.get("bstp_kor_isnm", ""),
        }

    def get_daily_ohlcv(self, code: str, days: int = 150) -> pd.DataFrame:
        """Adjusted daily candles, oldest first. The API returns max 100 rows per call."""
        rows: list[dict] = []
        end = datetime.now()
        while len(rows) < days:
            start = end - timedelta(days=140)
            data, _ = self.client.request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "FHKST03010100",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            )
            batch = [r for r in data.get("output2", []) if r and r.get("stck_bsop_date")]
            if not batch:
                break
            rows.extend(batch)
            oldest = min(r["stck_bsop_date"] for r in batch)
            end = datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)
            if len(batch) < 50:
                break
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(
            {
                "date": pd.to_datetime([r["stck_bsop_date"] for r in rows]),
                "open": [_num(r["stck_oprc"]) for r in rows],
                "high": [_num(r["stck_hgpr"]) for r in rows],
                "low": [_num(r["stck_lwpr"]) for r in rows],
                "close": [_num(r["stck_clpr"]) for r in rows],
                "volume": [_num(r["acml_vol"]) for r in rows],
            }
        )
        df = df.drop_duplicates("date").sort_values("date").set_index("date")
        return df[df["close"] > 0].tail(days)

    def is_trading_day(self, ymd: str) -> bool | None:
        """국내휴장일조회. 모의투자 서버는 미지원이라 None. KIS 권장대로 하루 1회만 호출할 것."""
        if self.cfg.is_paper:
            return None
        data, _ = self.client.request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/chk-holiday",
            "CTCA0903R",
            params={"BASS_DT": ymd, "CTX_AREA_NK": "", "CTX_AREA_FK": ""},
        )
        for row in data.get("output", []):
            if row.get("bass_dt") == ymd:
                return row.get("opnd_yn") == "Y"
        return None

    def get_volume_rank(self, limit: int = 30) -> list[tuple[str, str]]:
        """거래량 순위. 모의투자 서버는 지원하지 않는다."""
        data, _ = self.client.request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/volume-rank",
            "FHPST01710000",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_INPUT_DATE_1": "",
            },
        )
        out = []
        for r in data.get("output", [])[:limit]:
            code = r.get("mksc_shrn_iscd", "")
            if code:
                out.append((code, r.get("hts_kor_isnm", code)))
        return out

    # ------------------------------------------------------------ 계좌
    def get_balance(self) -> dict:
        holdings: list[dict] = []
        summary: dict = {}
        fk = nk = ""
        tr_cont = ""
        for _ in range(10):
            data, headers = self.client.request(
                "GET",
                "/uapi/domestic-stock/v1/trading/inquire-balance",
                self._tr("TTTC8434R", "VTTC8434R"),
                params={
                    "CANO": self.cfg.account_no,
                    "ACNT_PRDT_CD": self.cfg.account_product,
                    "AFHR_FLPR_YN": "N",
                    "OFL_YN": "",
                    "INQR_DVSN": "02",
                    "UNPR_DVSN": "01",
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN": "00",
                    "CTX_AREA_FK100": fk,
                    "CTX_AREA_NK100": nk,
                },
                tr_cont=tr_cont,
            )
            holdings.extend(h for h in data.get("output1", []) if _num(h.get("hldg_qty")) > 0)
            if data.get("output2"):
                summary = data["output2"][0]
            if headers.get("tr_cont") not in {"M", "F"}:
                break
            fk, nk, tr_cont = data.get("ctx_area_fk100", ""), data.get("ctx_area_nk100", ""), "N"
        return {
            # D+2 예수금: 매도대금 정산까지 반영된 실제 주문 가능 현금에 가장 가깝다.
            "cash": _num(summary.get("prvs_rcdl_excc_amt", summary.get("dnca_tot_amt"))),
            "total_equity": _num(summary.get("tot_evlu_amt")),
            "holdings": [
                {
                    "code": h["pdno"],
                    "name": h.get("prdt_name", h["pdno"]),
                    "qty": int(_num(h.get("hldg_qty"))),
                    "avg_price": _num(h.get("pchs_avg_pric")),
                    "price": _num(h.get("prpr")),
                }
                for h in holdings
            ],
        }

    def order_cash(self, side: str, code: str, qty: int, price: float | None = None) -> dict:
        """side: 'buy' | 'sell'. price=None 이면 시장가."""
        if qty <= 0:
            raise ValueError("qty must be positive")
        tr_id = {
            ("buy", False): "TTTC0012U",
            ("sell", False): "TTTC0011U",
            ("buy", True): "VTTC0012U",
            ("sell", True): "VTTC0011U",
        }[(side, self.cfg.is_paper)]
        body = {
            "CANO": self.cfg.account_no,
            "ACNT_PRDT_CD": self.cfg.account_product,
            "PDNO": code,
            "ORD_DVSN": "01" if price is None else "00",
            "ORD_QTY": str(int(qty)),
            "ORD_UNPR": "0" if price is None else str(int(price)),
        }
        data, _ = self.client.request("POST", "/uapi/domestic-stock/v1/trading/order-cash", tr_id, body=body)
        out = data.get("output", {})
        return {"order_no": out.get("ODNO", ""), "time": out.get("ORD_TMD", ""), "msg": data.get("msg1", "")}


__all__ = ["KISApi", "KISError"]

import pytest

from kis_ai_trader.config import KISConfig
from kis_ai_trader.kis import KISApi, KISClient, KISError


class FakeResp:
    def __init__(self, data, headers=None, status=200):
        self._data, self.headers, self.status_code = data, headers or {}, status
        self.text = str(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url, None, json))
        return FakeResp({"access_token": "tok", "expires_in": 86400})

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.calls.append((method, url, headers, json or params))
        return self.responses.pop(0)


def make(tmp_path, responses, env="paper"):
    cfg = KISConfig("key", "secret", "12345678", "01", env)
    session = FakeSession(responses)
    client = KISClient(cfg, token_cache=tmp_path / "tok.json", session=session, min_interval=0)
    return KISApi(client), session


def test_token_is_cached_across_clients(tmp_path):
    api, s = make(tmp_path, [FakeResp({"rt_cd": "0", "output": {"stck_prpr": "71,000", "prdy_ctrt": "1.5"}})])
    assert api.get_price("005930")["price"] == 71000
    assert sum(c[0] == "POST" for c in s.calls) == 1

    api2, s2 = make(tmp_path, [FakeResp({"rt_cd": "0", "output": {"stck_prpr": "1"}})])
    api2.get_price("005930")
    assert not any(c[0] == "POST" for c in s2.calls)


def test_error_and_token_refresh(tmp_path):
    api, s = make(
        tmp_path,
        [
            FakeResp({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token"}),
            FakeResp({"rt_cd": "0", "output": {"stck_prpr": "100"}}),
            FakeResp({"rt_cd": "1", "msg_cd": "APBK0013", "msg1": "주문 불가"}),
        ],
    )
    assert api.get_price("005930")["price"] == 100
    assert sum(c[0] == "POST" for c in s.calls) == 2
    with pytest.raises(KISError, match="APBK0013"):
        api.get_price("005930")


@pytest.mark.parametrize("env,side,tr", [("paper", "buy", "VTTC0012U"), ("paper", "sell", "VTTC0011U"),
                                         ("real", "buy", "TTTC0012U"), ("real", "sell", "TTTC0011U")])
def test_order_tr_ids_and_body(tmp_path, env, side, tr):
    api, s = make(tmp_path, [FakeResp({"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "0001"}})], env=env)
    res = api.order_cash(side, "005930", 3)
    _, url, headers, body = s.calls[-1]
    assert headers["tr_id"] == tr and url.endswith("/order-cash")
    assert body["ORD_DVSN"] == "01" and body["ORD_QTY"] == "3" and body["ORD_UNPR"] == "0"
    assert res["order_no"] == "0001"


def test_balance_parsing(tmp_path):
    api, _ = make(
        tmp_path,
        [
            FakeResp({
                "rt_cd": "0",
                "output1": [
                    {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "10", "pchs_avg_pric": "70000.5", "prpr": "72000"},
                    {"pdno": "000660", "prdt_name": "SK하이닉스", "hldg_qty": "0", "pchs_avg_pric": "0", "prpr": "0"},
                ],
                "output2": [{"prvs_rcdl_excc_amt": "5000000", "tot_evlu_amt": "5720000"}],
            })
        ],
    )
    bal = api.get_balance()
    assert bal["cash"] == 5_000_000 and len(bal["holdings"]) == 1
    assert bal["holdings"][0]["qty"] == 10


def test_daily_ohlcv_sorted_oldest_first(tmp_path):
    rows = [{"stck_bsop_date": f"202401{d:02d}", "stck_oprc": "1", "stck_hgpr": "2", "stck_lwpr": "1",
             "stck_clpr": str(d), "acml_vol": "10"} for d in range(20, 1, -1)]
    api, _ = make(tmp_path, [FakeResp({"rt_cd": "0", "output2": rows})])
    df = api.get_daily_ohlcv("005930", days=10)
    assert len(df) == 10 and df.index.is_monotonic_increasing and df["close"].iloc[-1] == 20

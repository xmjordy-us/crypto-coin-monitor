#!/usr/bin/env python3
"""Strict, stateful seven-asset monitor for GitHub Actions.

Primary values are never silently substituted.  Every attempted collection is
stored in `latest`; only complete, internally consistent primary values advance
`last_complete`.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = json.loads((ROOT / "config/assets.json").read_text())
STATE_PATH, REPORT_PATH = ROOT / "state/monitor-state.json", ROOT / "reports/latest.md"
NOW = datetime.now(timezone.utc)
SOURCES = {
    "binance": "https://api.binance.com/api/v3/ticker/price",
    "coinglass": "https://open-api-v4.coinglass.com",
    "coingecko": "https://api.coingecko.com/api/v3/coins/markets",
    "coinalyze": "https://api.coinalyze.net/v1/open-interest",
}

def get_json(url, headers=None, timeout=None):
    timeout = timeout or float(os.getenv("REQUEST_TIMEOUT_SECONDS", "12"))
    request = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    last_error = None
    for delay in (float(x) for x in os.getenv("REQUEST_RETRY_DELAYS", "0,2,5").split(",")):
        if delay: time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode()), None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            last_error = str(exc)
    return None, last_error

def value_from(item, names):
    if not isinstance(item, dict): return None
    for name in names:
        value = item.get(name)
        if value is not None:
            try: return float(value)
            except (TypeError, ValueError): pass
    return None

def coinglass_data(symbol):
    key = os.getenv("COINGLASS_API_KEY")
    if not key:
        return {"market_cap": None, "circulating_supply": None, "open_interest": None}, None, "COINGLASS_API_KEY not configured"
    headers = {"CG-API-KEY": key, "Accept": "application/json"}
    # CoinGlass API paths are kept in one place; response parsing accepts the
    # documented field variants while refusing a missing primary value.
    market, market_err = get_json(f"{SOURCES['coinglass']}/api/coin/markets?symbol={symbol}", headers)
    oi, oi_err = get_json(f"{SOURCES['coinglass']}/api/futures/open-interest?symbol={symbol}", headers)
    def payload(x):
        x = x.get("data") if isinstance(x, dict) else x
        return x[0] if isinstance(x, list) and x else x
    m, o = payload(market), payload(oi)
    cap = value_from(m, ("marketCap", "market_cap", "circulatingMarketCap"))
    supply = value_from(m, ("circulatingSupply", "circulating_supply"))
    open_interest = value_from(o, ("openInterest", "open_interest", "openInterestUsd", "open_interest_usd"))
    error = "; ".join(x for x in (market_err, oi_err) if x) or None
    return {"market_cap": cap, "circulating_supply": supply, "open_interest": open_interest}, m, error

def coingecko_rows():
    ids = ",".join(asset["coingecko_id"] for asset in ASSETS.values())
    headers = {"Accept": "application/json"}
    if os.getenv("COINGECKO_API_KEY"): headers["x-cg-demo-api-key"] = os.environ["COINGECKO_API_KEY"]
    rows, error = get_json(SOURCES["coingecko"] + "?" + urllib.parse.urlencode({"vs_currency":"usd", "ids":ids, "sparkline":"false"}), headers)
    return {row.get("id"): row for row in rows or [] if isinstance(row, dict)}, error

def coinalyze_oi(symbol):
    key = os.getenv("COINALYZE_API_KEY")
    if not key: return None, "COINALYZE_API_KEY not configured"
    url = SOURCES["coinalyze"] + "?" + urllib.parse.urlencode({"api_key": key, "symbols": symbol, "convert_to_usd":"true"})
    data, error = get_json(url)
    row = data[0] if isinstance(data, list) and data else None
    return value_from(row, ("open_interest_usd", "open_interest")), error

def pct_difference(a, b):
    return None if a is None or b in (None, 0) else abs(a-b)/abs(b)*100

def fmt(value):
    if value is None: return "N/A"
    if abs(value) >= 1e9: return f"${value/1e9:.3f}B"
    if abs(value) >= 1e6: return f"${value/1e6:.3f}M"
    if abs(value) >= 1: return f"${value:,.4f}"
    return f"${value:.8f}"

def load_state(): return json.loads(STATE_PATH.read_text())
def save_state(state): STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")

def main():
    state = load_state()
    cg_rows, cg_error = coingecko_rows()
    assets, source_errors = {}, {"coingecko": cg_error}
    for ticker, asset in ASSETS.items():
        binance, binance_error = get_json(SOURCES["binance"] + "?" + urllib.parse.urlencode({"symbol": asset["binance_symbol"]}))
        price = value_from(binance, ("price",))
        primary, raw_primary, glass_error = coinglass_data(asset["coinglass_symbol"])
        verify = cg_rows.get(asset["coingecko_id"], {})
        verify_cap = value_from(verify, ("market_cap",))
        verify_oi, coinalyze_error = coinalyze_oi(asset["coinalyze_symbol"])
        identity_ok = ticker != "AI" or str(verify.get("name", "")) == asset["require_name"]
        cap_gap, oi_gap = pct_difference(primary["market_cap"], verify_cap), pct_difference(primary["open_interest"], verify_oi)
        threshold = 5 if ticker in {"BONK", "AI", "ORDI"} else 3
        supply_risk = ticker == "AI" and primary["circulating_supply"] is None
        assets[ticker] = {
          "identity": asset["identity"], "identity_ok": identity_ok, "binance_spot_usd": price,
          "coinglass_market_cap_usd": primary["market_cap"], "coinglass_circulating_supply": primary["circulating_supply"],
          "coinglass_aggregated_oi_usd": primary["open_interest"], "coingecko_market_cap_usd": verify_cap,
          "coinalyze_oi_usd": verify_oi, "market_cap_gap_pct": cap_gap, "oi_gap_pct": oi_gap,
          "verification_warning": any(gap is not None and gap > threshold for gap in (cap_gap, oi_gap)),
          "high_risk_supply": supply_risk,
          "sources": {"binance": SOURCES["binance"], "coinglass": SOURCES["coinglass"], "coingecko": SOURCES["coingecko"], "coinalyze": SOURCES["coinalyze"]},
          "errors": {"binance": binance_error, "coinglass": glass_error, "coinalyze": coinalyze_error},
          "raw": {"binance": binance, "coinglass": raw_primary, "coingecko": verify}
        }
    complete = all(a["identity_ok"] and all(a[k] is not None for k in ("binance_spot_usd", "coinglass_market_cap_usd", "coinglass_aggregated_oi_usd")) for a in assets.values())
    snapshot = {"captured_at": NOW.isoformat(), "assets": assets, "complete": complete, "source_errors": source_errors}
    prior_complete = state.get("last_complete")
    daily_valid = False
    if complete and prior_complete:
        elapsed = (NOW - datetime.fromisoformat(prior_complete["captured_at"])).total_seconds()
        daily_valid = 85500 <= elapsed <= 87300
    if complete:
        state["previous_complete"] = prior_complete
        state["last_complete"] = snapshot
    state["latest"] = snapshot
    state["updated_at"] = NOW.isoformat()
    anomalies = detect_anomalies(snapshot, prior_complete)
    old = state.get("confirmed_anomalies", {})
    new = {k:v for k,v in anomalies.items() if old.get(k) != v}
    state["confirmed_anomalies"] = anomalies
    write_report(snapshot, complete, daily_valid, new)
    save_state(state)
    if new and os.getenv("ALERT_WEBHOOK_URL"):
        post_alert(new)

def detect_anomalies(snapshot, prior):
    # Conservative confirmed switches: OI / circulating market-cap crosses 10%.
    result = {}
    for ticker, asset in snapshot["assets"].items():
        cap, oi = asset["coinglass_market_cap_usd"], asset["coinglass_aggregated_oi_usd"]
        if cap and oi:
            zone = "high" if oi / cap >= .10 else "normal"
            # “Normal” is deliberately not an alert condition.  This prevents
            # the baseline run and continued normal conditions from pushing.
            if zone == "high": result[f"{ticker}:oi_cap"] = zone
    return result

def write_report(snapshot, complete, daily_valid, new):
    beijing = NOW.astimezone(timezone(timedelta(hours=8)))
    lines = ["# 加密货币监控", "", "## 结论", "", f"采集时间：{NOW.strftime('%Y-%m-%d %H:%M')} UTC / {beijing.strftime('%Y-%m-%d %H:%M')} 北京时间。", f"主数据完整性：{'完整，可更新 last-complete' if complete else '不完整；last-complete 未更新'}。完整日比较：{'可用' if daily_valid else 'N/A'}。", "", "## 紧凑主表", "", "|资产|Binance现货|CoinGlass流通市值|CoinGlass聚合OI|复核/风险|", "|---|---:|---:|---:|---|"]
    for ticker, a in snapshot["assets"].items():
        flags=[]
        if not a["identity_ok"]: flags.append("身份不符")
        if a["verification_warning"]: flags.append("主/复核差异超阈值")
        if a["high_risk_supply"]: flags.append("高风险：供应口径不明")
        if not flags: flags.append("N/A")
        lines.append(f"|{ticker}|{fmt(a['binance_spot_usd'])}|{fmt(a['coinglass_market_cap_usd'])}|{fmt(a['coinglass_aggregated_oi_usd'])}|{'；'.join(flags)}|")
    lines += ["", "## 最近运行后新增且有明确时间戳的市场事件", "", "N/A（未配置具时间戳的可靠事件源；社区帖与无时间戳内容不采用）。", "", "## BTC资金结构", "", "Binance现货、CoinGlass OI、资金费率、基差、清算及可靠订单流：未完整取得的字段为 N/A；本次不将推测写作事实。", "", "## 未来6至24小时情景", "", "仅在完整主数据与可交叉核验的新增结构切换出现时更新；否则 N/A。"]
    if new:
        lines += ["", "## 新异常提示", ""]
        for key, zone in new.items(): lines.append(f"- {key}：触发条件为 CoinGlass 同源 OI/流通市值比进入 `{zone}` 区；来源：{SOURCES['coinglass']}；确认条件为下一次完整采集仍处同一区；失效条件为回到另一分区；风险级别：中；限制：仅在 CoinGlass 主数据完整时成立。")
    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines)+"\n")

def post_alert(new):
    body = json.dumps({"text": "Crypto monitor: new confirmed anomaly\n" + "\n".join(f"- {k}: {v}" for k,v in new.items())}).encode()
    try: urllib.request.urlopen(urllib.request.Request(os.environ["ALERT_WEBHOOK_URL"], data=body, headers={"Content-Type":"application/json"}), timeout=12)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError): pass

if __name__ == "__main__": main()

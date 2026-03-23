#!/usr/bin/env python3
"""
BTC Widget Server - 本地HTTP服务器，提供币安永续合约分析
端口: 8765
"""

import json
import threading
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen
from urllib.error import URLError
import urllib.request
import time

BINANCE_BASE = "https://fapi.binance.com"

def smart_round(v, sig=5):
    """根据价格大小自动决定小数位，避免小价格显示0.00"""
    if v == 0:
        return 0
    import math
    digits = sig - int(math.floor(math.log10(abs(v)))) - 1
    digits = max(2, min(digits, 10))
    return round(v, digits)




# 中文名 -> 交易对 映射（用户搜中文时自动补全）
CN_NAME_MAP = {
    "比特币": "BTC", "以太坊": "ETH", "以太": "ETH",
    "索拉纳": "SOL", "狗狗币": "DOGE", "柴犬": "SHIB",
    "波卡": "DOT", "链接": "LINK", "雪崩": "AVAX",
    "波场": "TRX", "莱特币": "LTC", "币安币": "BNB",
    "人生": "NIGHT", "夜晚": "NIGHT",
    "OP": "OP", "ARB": "ARB", "PEPE": "PEPE",
    "土狗": "", "聪明钱": "", "鲨鱼": "DOGE",
}

HIGHER_INTERVAL = {
    "5m": "15m",
    "15m": "1h",
    "1h": "4h",
    "4h": "1d",
    "1d": "1w"
}


def binance_get(path, params=None, timeout=4):
    """所有对binance的请求走这里，timeout严格限制，Windows下用socket超时双保险"""
    url = BINANCE_BASE + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "btc-widget/1.0"})
    import socket
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    finally:
        socket.setdefaulttimeout(old_timeout)


def get_symbols():
    data = binance_get("/fapi/v1/exchangeInfo")
    syms = [s["symbol"] for s in data["symbols"] if s["contractType"] == "PERPETUAL" and s["status"] == "TRADING"]
    # 部分合约symbol含中文（如"币安人生USDT"），正常返回即可
    return syms


def get_klines(symbol, interval, limit=120):
    data = binance_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": str(limit)})
    closes = [float(k[4]) for k in data]
    highs = [float(k[2]) for k in data]
    lows = [float(k[3]) for k in data]
    return closes, highs, lows


def calc_ema(prices, period):
    ema = []
    k = 2 / (period + 1)
    for i, p in enumerate(prices):
        if i == 0:
            ema.append(p)
        else:
            ema.append(p * k + ema[-1] * (1 - k))
    return ema


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def calc_atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    if not trs:
        return 0.0
    return sum(trs[-period:]) / min(len(trs), period)


def analyze_trend(closes, highs, lows):
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)
    rsi = calc_rsi(closes)
    atr = calc_atr(highs, lows, closes)

    current_price = closes[-1]
    e20 = ema20[-1]
    e50 = ema50[-1]

    # 趋势判断
    if current_price > e20 > e50 and e20 > ema20[-5]:
        trend = "多"
        score = 1
    elif current_price < e20 < e50 and e20 < ema20[-5]:
        trend = "空"
        score = -1
    else:
        trend = "中性"
        score = 0

    # 状态
    if rsi > 70:
        state = "超买"
    elif rsi < 30:
        state = "超卖"
    elif trend == "多" and rsi > 50:
        state = "多头延续"
    elif trend == "空" and rsi < 50:
        state = "空头延续"
    elif trend == "多":
        state = "多头偏弱"
    elif trend == "空":
        state = "空头偏弱"
    else:
        state = "震荡"

    # 支撑阻力
    recent_lows = sorted(lows[-20:])
    recent_highs = sorted(highs[-20:])
    support = sum(recent_lows[:3]) / 3
    resistance = sum(recent_highs[-3:]) / 3

    return {
        "trend": trend,
        "score": score,
        "rsi": round(rsi, 2),
        "atr": smart_round(atr),
        "support": smart_round(support),
        "resistance": smart_round(resistance),
        "state": state,
        "current_price": smart_round(current_price),
        "ema20": smart_round(e20),
        "ema50": smart_round(e50),
    }


def get_oi_change(symbol):
    try:
        data = binance_get("/futures/data/openInterestHist", {
            "symbol": symbol, "period": "5m", "limit": "2"
        })
        if len(data) >= 2:
            oi_now = float(data[-1]["sumOpenInterestValue"])
            oi_prev = float(data[-2]["sumOpenInterestValue"])
            if oi_prev > 0:
                return round((oi_now - oi_prev) / oi_prev * 100, 4)
    except Exception:
        pass
    return 0.0


def get_taker_ratio(symbol):
    try:
        data = binance_get("/futures/data/takerlongshortRatio", {
            "symbol": symbol, "period": "5m", "limit": "1"
        })
        if data:
            return round(float(data[-1]["buySellRatio"]), 4)
    except Exception:
        pass
    return 1.0


def get_top_position_ratio(symbol):
    try:
        data = binance_get("/futures/data/topLongShortPositionRatio", {
            "symbol": symbol, "period": "5m", "limit": "1"
        })
        if data:
            return round(float(data[-1]["longShortRatio"]), 4)
    except Exception:
        pass
    return 1.0


def get_funding_rate(symbol):
    try:
        data = binance_get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return round(float(data["lastFundingRate"]), 8)
    except Exception:
        pass
    return 0.0


def analyze(symbol, interval):
    closes, highs, lows = get_klines(symbol, interval, limit=120)
    chart = [smart_round(c) for c in closes[-60:]]

    current = analyze_trend(closes, highs, lows)
    current_price = current["current_price"]
    atr = current["atr"]

    # 上级周期
    higher_interval = HIGHER_INTERVAL.get(interval, "1d")
    h_closes, h_highs, h_lows = get_klines(symbol, higher_interval, limit=60)
    higher = analyze_trend(h_closes, h_highs, h_lows)
    higher_trend = higher["trend"]

    # 链上数据
    oi_change = get_oi_change(symbol)
    taker = get_taker_ratio(symbol)
    top_pos = get_top_position_ratio(symbol)
    last_fr = get_funding_rate(symbol)

    # 费率状态
    if last_fr > 0.0005:
        fr_status = "偏多"
    elif last_fr < -0.0005:
        fr_status = "偏空"
    else:
        fr_status = "中性"

    # 信号分级
    trend = current["trend"]
    rsi = current["rsi"]

    # 方向一致性
    same_direction = (trend == higher_trend and trend != "中性")
    opposite = (trend != "中性" and higher_trend != "中性" and trend != higher_trend)

    # 3核心指标
    if trend == "多":
        oi_ok = oi_change > 0.2
        taker_ok = taker > 1.05
        top_ok = top_pos > 1.0
    elif trend == "空":
        oi_ok = oi_change > 0.2  # OI增加也算空头入场
        taker_ok = taker < 0.95
        top_ok = top_pos < 1.0
    else:
        oi_ok = taker_ok = top_ok = False

    core_count = sum([oi_ok, taker_ok, top_ok])

    if opposite:
        signal_level = "反向"
    elif same_direction and core_count == 3:
        signal_level = "强"
    elif same_direction and core_count == 2:
        signal_level = "中"
    else:
        signal_level = "弱"

    # 止盈止损
    entry = current_price
    if trend == "多" and signal_level in ("强", "中"):
        stop_loss = smart_round(current["support"] - atr * 0.5)
        risk = entry - stop_loss
        tp1 = round(min(current["resistance"], entry + risk * 1.5), 4)
        tp2 = smart_round(entry + atr * 2)
        tp3 = smart_round(entry + atr * 3.5)
        entry_price = smart_round(entry)
    elif trend == "空" and signal_level in ("强", "中"):
        stop_loss = smart_round(current["resistance"] + atr * 0.5)
        risk = stop_loss - entry
        tp1 = round(max(current["support"], entry - risk * 1.5), 4)
        tp2 = smart_round(entry - atr * 2)
        tp3 = smart_round(entry - atr * 3.5)
        entry_price = smart_round(entry)
    else:
        entry_price = stop_loss = tp1 = tp2 = tp3 = None

    # 大白话
    reasons_plain = []

    # 大方向
    if higher_trend == "多":
        reasons_plain.append(
            f"大方向（{higher_interval}）：偏多，均线向上走，价格在大均线上方"
        )
    elif higher_trend == "空":
        reasons_plain.append(
            f"大方向（{higher_interval}）：偏空，均线向下走，价格在大均线下方"
        )
    else:
        reasons_plain.append(
            f"大方向（{higher_interval}）：中性，均线纠缠，方向不明"
        )

    # 当前盘面
    momentum = "偏强" if rsi > 55 else ("偏弱" if rsi < 45 else "中性")
    reasons_plain.append(
        f"当前盘面（{interval}）：{current['state']}，RSI {rsi:.0f}，动能{momentum}"
    )

    # OI
    oi_desc = "新资金进场" if oi_change > 0.2 else ("仓位在撤退" if oi_change < -0.2 else "仓位平稳")
    reasons_plain.append(
        f"仓位变化：OI变化{oi_change:+.2f}%，{oi_desc}"
    )

    # Taker
    taker_desc = "买的人更多，主动买盘强" if taker > 1.05 else ("卖的人更多，主动砸盘强" if taker < 0.95 else "买卖力量相当")
    reasons_plain.append(
        f"买卖力度：Taker比{taker:.3f}，{taker_desc}"
    )

    # 大户
    top_desc = "大户偏向做多" if top_pos > 1.05 else ("大户偏向做空" if top_pos < 0.95 else "大户仓位中性，没有明确表态")
    reasons_plain.append(
        f"大户动向：大户持仓比{top_pos:.3f}，{top_desc}"
    )

    # 资金费率
    fr_desc = "做多的人太多，正在给做空的人补贴" if last_fr > 0.0005 else (
        "做空的人太多，正在给做多的人补贴" if last_fr < -0.0005 else "费率正常，多空相对平衡"
    )
    reasons_plain.append(
        f"资金费率：{last_fr * 100:.4f}%，{fr_desc}"
    )

    # 综合判断
    if signal_level == "强":
        summary = f"综合判断：{trend}向信号强，大小周期共振，核心指标全面支持{trend}，可以考虑顺势入场"
    elif signal_level == "中":
        summary = f"综合判断：{trend}向信号中等，大周期方向支持，部分指标尚未完全配合，可轻仓参与"
    elif signal_level == "反向":
        summary = f"综合判断：当前{interval}周期偏{trend}，但{higher_interval}大方向为{higher_trend}，方向相悖，建议观望或等待方向统一"
    else:
        summary = f"综合判断：信号偏弱，方向不明确或大周期支持不足，建议观望等待更好的入场时机"
    reasons_plain.append(summary)

    # 盈亏比
    rr = None
    if entry_price and stop_loss and tp1:
        risk_amt = abs(entry_price - stop_loss)
        reward_amt = abs(tp1 - entry_price)
        rr = round(reward_amt / risk_amt, 2) if risk_amt > 0 else None

    return {
        "symbol": symbol,
        "interval": interval,
        "close": smart_round(current_price),
        "trend": trend,
        "score": current["score"],
        "rsi": rsi,
        "atr": atr,
        "support": smart_round(current["support"]),
        "resistance": smart_round(current["resistance"]),
        "state": current["state"],
        "higher_interval": higher_interval,
        "higher_trend": higher_trend,
        "signal_level": signal_level,
        "summary": summary,
        "reasons_plain": reasons_plain,
        "oi_change": oi_change,
        "taker_ratio": taker,
        "top_pos_ratio": top_pos,
        "last_funding_rate": last_fr,
        "funding_state": fr_status,
        "entry_price": smart_round(entry_price) if entry_price else None,
        "stop_loss": smart_round(stop_loss) if stop_loss else None,
        "tp1": smart_round(tp1) if tp1 else None,
        "tp2": smart_round(tp2) if tp2 else None,
        "tp3": smart_round(tp3) if tp3 else None,
        "rr": rr,
        "chart": chart,
        "timestamp": int(time.time()),
    }



# ========== 三层扫描架构 ==========

def _layer1_filter(top_n=50):
    """第一层：1次请求拿全市场数据，筛出候选池（无K线请求）
    top_n 控制主通道数量，bonus通道固定10个（高波动补充）
    """
    tickers = binance_get("/fapi/v1/ticker/24hr", timeout=8)
    candidates = []
    for t in tickers:
        sym = t["symbol"]
        if not sym.endswith("USDT"):
            continue
        try:
            qvol = float(t["quoteVolume"])
            price = float(t["lastPrice"])
            change_pct = float(t["priceChangePercent"])
            high = float(t["highPrice"])
            low = float(t["lowPrice"])
            if price <= 0 or low <= 0:
                continue
            volatility = (high - low) / low * 100
            candidates.append({
                "symbol": sym,
                "price": price,
                "qvol": qvol,
                "change_pct": change_pct,
                "volatility": volatility,
            })
        except Exception:
            continue

    if not candidates:
        return []

    by_vol = sorted(candidates, key=lambda x: -x["qvol"])
    # 主通道：按top_n取（真正响应参数）
    main_pool = by_vol[:top_n]
    main_syms = {c["symbol"] for c in main_pool}

    # 补漏通道：高波动但不在主通道，固定限10个（大幅缩减）
    bonus = [c for c in by_vol[top_n:] if c["volatility"] > 8.0][:10]

    return main_pool + bonus


def _layer2_direction(symbol, interval, price_hint, ticker_data=None):
    """第二层：只拉20根K线，快速判断方向（EMA+结构），严格1.5s超时"""
    try:
        data = binance_get("/fapi/v1/klines", {
            "symbol": symbol, "interval": interval, "limit": "20"
        }, timeout=3)
        closes = [float(k[4]) for k in data]
        highs = [float(k[2]) for k in data]
        lows = [float(k[3]) for k in data]
        if len(closes) < 20:
            return None

        # EMA20 和 EMA55（用25根近似）
        def ema(data, n):
            k = 2/(n+1); e = data[0]
            for v in data[1:]: e = v*k + e*(1-k)
            return e

        e20 = ema(closes, 20)
        e_fast = ema(closes, 8)   # 短期EMA
        price = closes[-1]

        # 结构：最近5根高低点
        recent_highs = highs[-5:]
        recent_lows = lows[-5:]
        structure_up = recent_highs[-1] > recent_highs[0] and recent_lows[-1] > recent_lows[0]
        structure_dn = recent_highs[-1] < recent_highs[0] and recent_lows[-1] < recent_lows[0]

        # RSI简化版
        gains, losses = [], []
        for i in range(1, 14):
            if i >= len(closes): break
            d = closes[-i] - closes[-i-1]
            (gains if d>0 else losses).append(abs(d))
        avg_g = sum(gains)/len(gains) if gains else 0.01
        avg_l = sum(losses)/len(losses) if losses else 0.01
        rsi = 100 - 100/(1 + avg_g/avg_l)

        # 得分
        score = 0
        if price > e20: score += 1
        if e_fast > e20: score += 1
        if structure_up: score += 1
        if rsi > 55: score += 1
        if price < e20: score -= 1
        if e_fast < e20: score -= 1
        if structure_dn: score -= 1
        if rsi < 45: score -= 1

        trend = "多" if score >= 2 else ("空" if score <= -2 else "中性")
        if trend == "中性":
            return None  # 方向不明，直接丢弃

        result = {
            "symbol": symbol,
            "close": smart_round(price),
            "trend": trend,
            "score": score,
            "strength": abs(score),
            "rsi": round(rsi, 1),
        }
        if ticker_data:
            result["change_pct"] = round(ticker_data.get("change_pct", 0), 2)
            result["qvol"] = ticker_data.get("qvol", 0)
        return result
    except Exception:
        return None


def get_overview(interval="15m", top_n=50):
    """三层扫描：第一层全市场筛选→第二层并发方向判断→返回方向明确列表
    top_n: 主通道数量，默认50，加bonus最多60个symbol
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 第一层：全市场快速筛选（1次请求）
    try:
        candidates = _layer1_filter(top_n)
    except Exception:
        candidates = []

    if not candidates:
        return {"interval": interval, "total_scanned": 0, "longs": [], "shorts": [], "timestamp": int(time.time())}

    syms = [c["symbol"] for c in candidates]
    price_map = {c["symbol"]: c["price"] for c in candidates}
    ticker_map = {c["symbol"]: c for c in candidates}

    # 第二层：并发方向判断（只拉20根K线）
    # workers=20，最多60个symbol，约3波 × 3s timeout = 9s上限（快网络~2s）
    results = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {
            ex.submit(_layer2_direction, sym, interval, price_map[sym], ticker_map.get(sym)): sym
            for sym in syms
        }
        for f in as_completed(futures, timeout=30):
            try:
                r = f.result()
                if r:
                    results.append(r)
            except Exception:
                pass

    longs = sorted([r for r in results if r["trend"] == "多"], key=lambda x: -x["strength"])
    shorts = sorted([r for r in results if r["trend"] == "空"], key=lambda x: -x["strength"])

    return {
        "interval": interval,
        "total_scanned": len(syms),
        "longs": longs,
        "shorts": shorts,
        "timestamp": int(time.time()),
    }


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程HTTP服务器：每个请求独立线程，扫描不阻塞其他请求"""
    daemon_threads = True


class WidgetHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默日志

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/health":
            self.send_text("ok")

        elif path == "/":
            # 返回UI
            ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "widget_ui.html")
            if os.path.exists(ui_path):
                with open(ui_path, "r", encoding="utf-8") as f:
                    html = f.read()
                self.send_html(html)
            else:
                self.send_text("widget_ui.html not found", 404)

        elif path == "/api/symbols":
            try:
                symbols = get_symbols()
                # 加入中文别名，方便搜索
                cn_entries = []
                for cn, en in CN_NAME_MAP.items():
                    if en:
                        matches = [s for s in symbols if s.startswith(en+'USDT') or s == en+'USDT']
                        for m in matches:
                            cn_entries.append({"symbol": m, "cn": cn})
                # 把含中文的symbol也加进cn_entries
                for s in symbols:
                    if any('一' <= c <= '鿿' for c in s):
                        cn_entries.append({"symbol": s, "cn": s})
                self.send_json({"symbols": symbols, "cn_map": cn_entries})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/api/overview":
            interval = params.get("interval", ["15m"])[0]
            top_n = int(params.get("top_n", ["50"])[0])
            try:
                result = get_overview(interval, top_n)
                self.send_json(result)
            except Exception as e:
                import traceback
                self.send_json({"error": str(e), "trace": traceback.format_exc()}, 500)

        elif path == "/api/analyze":
            symbol = params.get("symbol", ["BTCUSDT"])[0].upper()
            interval = params.get("interval", ["15m"])[0]
            try:
                result = analyze(symbol, interval)
                self.send_json(result)
            except Exception as e:
                import traceback
                self.send_json({"error": str(e), "trace": traceback.format_exc()}, 500)

        else:
            self.send_text("Not Found", 404)


def run_server(port=8765):
    server = ThreadedHTTPServer(("127.0.0.1", port), WidgetHandler)
    print(f"[BTC Widget] Server running at http://127.0.0.1:{port} (多线程模式)")
    server.serve_forever()


def launch_with_pywebview(port=8765):
    import webview
    window = webview.create_window(
        "BTC Widget",
        f"http://127.0.0.1:{port}",
        width=900,
        height=900,
        resizable=True,
    )
    webview.start()


def launch_with_tkinter_fallback():
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.title("BTC Widget - 缺少依赖")
        root.geometry("400x200")
        label = tk.Label(
            root,
            text="请安装 pywebview 以使用桌面窗口模式：\n\npip install pywebview\n\n或直接浏览器访问：\nhttp://127.0.0.1:8765",
            justify="center",
            pady=20,
        )
        label.pack(expand=True)
        btn = tk.Button(root, text="关闭", command=root.destroy)
        btn.pack(pady=10)
        root.mainloop()
    except Exception:
        print("请安装 pywebview: pip install pywebview")
        print("或直接浏览器访问: http://127.0.0.1:8765")


if __name__ == "__main__":
    PORT = 8765

    # 先启动服务器线程
    t = threading.Thread(target=run_server, args=(PORT,), daemon=True)
    t.start()
    time.sleep(0.5)  # 等待服务器就绪

    # 尝试 pywebview 启动
    try:
        import webview
        print("[BTC Widget] 使用 pywebview 桌面窗口")
        launch_with_pywebview(PORT)
    except ImportError:
        print("[BTC Widget] 未安装 pywebview，使用 tkinter 提示")
        launch_with_tkinter_fallback()
        # tkinter 退出后，服务器仍在后台，保持主线程存活
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[BTC Widget] 已停止")

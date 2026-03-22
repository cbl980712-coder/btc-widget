# -*- coding: utf-8 -*-
"""
本地 API 代理服务器 v2 - 支持所有合约币种 + 搜索
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json, time, math, urllib.request, urllib.parse, urllib.error

BASE_FAPI = "https://fapi.binance.com"
TIMEOUT = 12

def to_float(x, default=0.0):
    try: return float(x)
    except: return default

def pct_change(new, old):
    if old == 0: return 0.0
    return (new - old) / old * 100.0

def safe_div(a, b, default=0.0):
    if b == 0: return default
    return a / b

def request_json(path, params=None):
    url = f"{BASE_FAPI}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if i == 2: raise RuntimeError(f"请求失败: {url} {e}")
            time.sleep(0.6 * (i + 1))

def ema(values, period):
    if not values: return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def sma(values, period):
    out = []; s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period: s -= values[i - period]
        out.append(s / period if i >= period - 1 else v)
    return out

def rsi(values, period=14):
    if len(values) < 2: return [50.0] * len(values)
    gains = [0.0]; losses = [0.0]
    for i in range(1, len(values)):
        diff = values[i] - values[i-1]
        gains.append(max(diff, 0.0))
        losses.append(abs(min(diff, 0.0)))
    ag = sma(gains, period); al = sma(losses, period)
    return [100.0 if l == 0 else 100.0 - 100.0/(1.0+g/l) for g, l in zip(ag, al)]

def atr(highs, lows, closes, period=14):
    if not highs: return []
    trs = []; prev = closes[0]
    for h, l, c in zip(highs, lows, closes):
        trs.append(max(h-l, abs(h-prev), abs(l-prev))); prev = c
    return sma(trs, period)

def macd(values, fast=12, slow=26, signal=9):
    fe = ema(values, fast); se = ema(values, slow)
    ml = [f-s for f,s in zip(fe, se)]
    sl = ema(ml, signal)
    return ml, sl, [m-s for m,s in zip(ml, sl)]

def get_klines(symbol, interval="15m", limit=220):
    data = request_json("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return [{"open_time": int(x[0]), "open": to_float(x[1]), "high": to_float(x[2]),
             "low": to_float(x[3]), "close": to_float(x[4]), "volume": to_float(x[5]),
             "taker_buy_volume": to_float(x[9])} for x in data]

def analyze_trend(kl, sr_len=30):
    closes = [x["close"] for x in kl]
    highs  = [x["high"]  for x in kl]
    lows   = [x["low"]   for x in kl]
    ema21 = ema(closes,21)[-1]; ema55 = ema(closes,55)[-1]; ema200 = ema(closes,200)[-1]
    atr14 = atr(highs,lows,closes,14)[-1]
    rsi14 = rsi(closes,14)[-1]
    _,_,mh = macd(closes)
    close = closes[-1]
    support = min(lows[-sr_len:]); resistance = max(highs[-sr_len:])
    range_pct = safe_div(resistance-support, close)*100.0
    score = 0
    if close > ema200: score += 2
    else: score -= 2
    if ema21 > ema55: score += 2
    else: score -= 2
    if mh[-1] > 0: score += 1
    else: score -= 1
    if rsi14 > 55: score += 1
    elif rsi14 < 45: score -= 1
    trend = "偏多" if score>=3 else ("偏空" if score<=-3 else "中性")
    atr_pct = safe_div(atr14, close)*100.0
    if abs(score)>=4 and atr_pct>0.35: state="趋势延续"
    elif abs(score)<=2 and range_pct<3.0: state="震荡整理"
    elif atr_pct<0.20: state="缩量等待"
    else: state="方向待确认"
    return {"trend":trend,"trend_score":score,"close":close,
            "ema21":ema21,"ema55":ema55,"ema200":ema200,
            "atr":atr14,"rsi":rsi14,"macd_hist":mh[-1],
            "support":support,"resistance":resistance,"state":state}

def run_analysis(symbol="BTCUSDT"):
    kl = get_klines(symbol)
    oi_now   = request_json("/fapi/v1/openInterest", {"symbol": symbol})
    oi_hist  = request_json("/futures/data/openInterestHist", {"symbol":symbol,"period":"5m","limit":30})
    top_pos  = request_json("/futures/data/topLongShortPositionRatio", {"symbol":symbol,"period":"5m","limit":30})
    top_acc  = request_json("/futures/data/topLongShortAccountRatio", {"symbol":symbol,"period":"5m","limit":30})
    global_ls= request_json("/futures/data/globalLongShortAccountRatio", {"symbol":symbol,"period":"5m","limit":30})
    taker    = request_json("/futures/data/takerlongshortRatio", {"symbol":symbol,"period":"5m","limit":30})
    basis    = request_json("/futures/data/basis", {"pair":symbol,"contractType":"PERPETUAL","period":"5m","limit":30})
    premium  = request_json("/fapi/v1/premiumIndex", {"symbol": symbol})
    funding_hist = request_json("/fapi/v1/fundingRate", {"symbol":symbol,"limit":10})

    t = analyze_trend(kl)
    oi_hl = to_float(oi_hist[-1]["sumOpenInterestValue"]) if oi_hist else 0.0
    oi_hp = to_float(oi_hist[-2]["sumOpenInterestValue"]) if len(oi_hist)>=2 else oi_hl
    oi_change = pct_change(oi_hl, oi_hp)
    taker_l = taker[-1] if taker else {}
    taker_ratio = to_float(taker_l.get("buySellRatio"),1.0)
    top_pos_ratio = to_float(top_pos[-1]["longShortRatio"]) if top_pos else 1.0
    top_acc_ratio = to_float(top_acc[-1]["longShortRatio"]) if top_acc else 1.0
    global_ratio  = to_float(global_ls[-1]["longShortRatio"]) if global_ls else 1.0
    basis_l = basis[-1] if basis else {}
    basis_rate = to_float(basis_l.get("basisRate"),0.0)
    last_fr = to_float(premium.get("lastFundingRate"),0.0)
    fv = [to_float(x["fundingRate"]) for x in funding_hist] if funding_hist else [0.0]
    favg = sum(fv)/len(fv)
    v4 = fv[-4:]
    if all(x>0 for x in v4): fstate="连续正费率"
    elif all(x<0 for x in v4): fstate="连续负费率"
    else: fstate="正负交替"

    cl = last_fr>0.0005 and favg>0.0003
    cs = last_fr<-0.0005 and favg<-0.0003
    lc = (t["trend"]=="偏多" and oi_change>0.2 and taker_ratio>1.05 and top_pos_ratio>1.0)
    sc = (t["trend"]=="偏空" and oi_change>0.2 and taker_ratio<0.95 and top_pos_ratio<1.0)

    reasons = []
    if lc: reasons.append("价格结构偏多，仓位增加，主动买盘更强，大户仓位偏多")
    if sc: reasons.append("价格结构偏空，仓位增加，主动卖盘更强，大户仓位偏空")
    if t["trend"]=="偏多" and oi_change<-0.2: reasons.append("价格走强但仓位下降，更像空头回补")
    if t["trend"]=="偏空" and oi_change<-0.2: reasons.append("价格走弱但仓位下降，更像多头出清")
    if cl: reasons.append("资金费率持续偏正，多头拥挤度偏高")
    if cs: reasons.append("资金费率持续偏负，空头拥挤度偏高")
    if global_ratio>1.3 and top_pos_ratio<1.0: reasons.append("大众偏多但大户未同步，防反杀")
    if global_ratio<0.8 and top_pos_ratio>1.0: reasons.append("大众偏空但大户未同步，防反抽")

    if t["state"]=="震荡整理": action="观望"; conf="中性"; reasons.append("当前震荡，不适合追单")
    elif t["state"]=="缩量等待": action="观望"; conf="中性"; reasons.append("波动收缩，等待放量方向")
    else:
        if lc and not cl: action="建议做多"; conf="强"
        elif sc and not cs: action="建议做空"; conf="强"
        elif lc and cl: action="偏多但别追"; conf="中"; reasons.append("方向偏多，但多头过于拥挤")
        elif sc and cs: action="偏空但别追"; conf="中"; reasons.append("方向偏空，但空头过于拥挤")
        elif t["trend"]=="偏多": action="偏多等确认"; conf="弱"
        elif t["trend"]=="偏空": action="偏空等确认"; conf="弱"
        else: action="观望"; conf="弱"

    entry = t["close"]
    if "做多" in action or action.startswith("偏多"):
        sl=t["support"]-t["atr"]*0.5; risk=abs(entry-sl)
        tp1=entry+risk*1.5; tp2=entry+risk*2.5
    elif "做空" in action or action.startswith("偏空"):
        sl=t["resistance"]+t["atr"]*0.5; risk=abs(sl-entry)
        tp1=entry-risk*1.5; tp2=entry-risk*2.5
    else:
        if t["trend"]=="偏多":
            sl=t["support"]-t["atr"]*0.5; risk=abs(entry-sl)
            tp1=entry+risk*1.2; tp2=entry+risk*2.0
        else:
            sl=t["resistance"]+t["atr"]*0.5; risk=abs(sl-entry)
            tp1=entry-risk*1.2; tp2=entry-risk*2.0

    smap={"建议做多":"大方向偏多，且看到新多在进场，这类单子可以做。",
          "建议做空":"大方向偏空，且看到新空在进场，这类单子可以做。",
          "偏多但别追":"方向还是多，但已经有点挤了，更适合等回踩。",
          "偏空但别追":"方向还是空，但已经有点挤了，更适合等反抽。",
          "偏多等确认":"盘面偏多，但还差一点确认，别急着追。",
          "偏空等确认":"盘面偏空，但还差一点确认，别急着追。"}
    summary = smap.get(action,"现在更像等待区，先别急着出手。")

    return {
        "symbol":symbol, "action":action, "confidence":conf,
        "summary":summary, "reasons":reasons,
        "entry":entry, "stop_loss":sl, "take_profit_1":tp1, "take_profit_2":tp2,
        "rr":safe_div(abs(tp2-entry),abs(sl-entry)),
        "trend":t,
        "structure":{"oi_change_pct":oi_change,"taker_ratio":taker_ratio,
                     "top_pos_ratio":top_pos_ratio,"top_acc_ratio":top_acc_ratio,
                     "global_ratio":global_ratio,"last_funding_rate":last_fr,
                     "funding_avg":favg,"funding_state":fstate,"basis_rate":basis_rate},
        "chart":{"closes":[x["close"] for x in kl[-50:]],
                 "times":[x["open_time"] for x in kl[-50:]]},
        "updated_at":int(time.time())
    }

# 缓存所有合约列表
_symbols_cache = None
_symbols_ts = 0

def get_all_symbols():
    global _symbols_cache, _symbols_ts
    now = time.time()
    if _symbols_cache and now - _symbols_ts < 3600:
        return _symbols_cache
    data = request_json("/fapi/v1/exchangeInfo")
    syms = []
    for s in data.get("symbols", []):
        if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL":
            syms.append({
                "symbol": s["symbol"],
                "baseAsset": s.get("baseAsset",""),
                "quoteAsset": s.get("quoteAsset","")
            })
    syms.sort(key=lambda x: x["symbol"])
    _symbols_cache = syms
    _symbols_ts = now
    return syms


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.end_headers()

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/symbols":
            try:
                syms = get_all_symbols()
                self.send_json(200, syms)
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == "/api/analyze":
            symbol = params.get("symbol",["BTCUSDT"])[0].upper()
            try:
                data = run_analysis(symbol)
                self.send_json(200, data)
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == "/health":
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin","*")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == "__main__":
    port = 8765
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"[合约面板] 服务器已启动: http://127.0.0.1:{port}")
    print("[合约面板] 按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[合约面板] 已停止")

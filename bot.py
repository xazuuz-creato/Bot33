import os
import sys
import aiohttp
import asyncio
import hmac
import hashlib
import base64
import json
import pandas as pd
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

# --- إعدادات التوقيت المحلي لمدينة الرياض ---
RIYADH_TZ = timezone(timedelta(hours=3))

def get_riyadh_time():
    return datetime.now(RIYADH_TZ).strftime('%Y-%m-%d %I:%M:%S %p')

# --- خادم الويب الفوري المستقر لمنع إغلاق Render نهائياً ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        html = """
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head><meta charset="UTF-8"><title>Ultimate Hybrid Bot Running</title></head>
        <body style="background:#0b0e11; color:#0ecb81; text-align:center; font-family:Arial; padding:50px;">
            <h1>🚀 Ultimate Hybrid OKX Bot يعمل بنجاح واستقرار تام 24/7 بتوقيت الرياض</h1>
        </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))
    
    def log_message(self, format, *args):
        pass

def run_fast_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

# تشغيل خادم الاستقرار في خلفية مستقلة تماماً
server_thread = Thread(target=run_fast_server, daemon=True)
server_thread.start()

BOT_SETTINGS = {
    "is_running": True,
    "trade_amount_usdt": 100.0,
    "target_tp_pct": 0.03,
    "stop_loss_pct": 0.007,
    "excluded_symbols": []
}

BOT_STATS = {
    "total_trades": 0,
    "winning_trades": 0,
    "losing_trades": 0,
    "total_profit_usdt": 0.0,
    "active_positions_count": 0
}

# --- بيانات اتصال الـ API الخاصة بمنصة OKX ---
API_KEY = '2bf148cb-c010-49fb-bffc-79f72720dfae'
SECRET_KEY = '1FBA3210BF6500BB1A1570EE9EC41A5C'
PASSPHRASE = 'Aa11223344##'

MAJOR_COINS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT"]

def get_okx_headers(method, request_path, body=""):
    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    message = timestamp + method.upper() + request_path + body
    mac = hmac.new(SECRET_KEY.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
    signature = base64.b64encode(mac.digest()).decode('utf-8')
    return {
        "Content-Type": "application/json",
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "x-simulated-trading": "1"
    }

async def fetch_okx_candles(session, symbol):
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar=15m&limit=210"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("code") == "0" and data.get("data"):
                    raw = data["data"]
                    candles = []
                    for c in reversed(raw):
                        candles.append({
                            'time': int(c[0]),
                            'open': float(c[1]),
                            'high': float(c[2]),
                            'low': float(c[3]),
                            'close': float(c[4]),
                            'volume': float(c[5])
                        })
                    return candles
    except Exception as e:
        print(f"[{get_riyadh_time()}] ⚠️ [شبكة] خطأ سحب شموع {symbol}: {e}", flush=True, file=sys.stdout)
    return None

def calculate_ema(data, period):
    multiplier = 2 / (period + 1)
    ema_list = []
    for i, val in enumerate(data):
        if i == 0:
            ema_list.append(val)
        else:
            ema_list.append((val - ema_list[-1]) * multiplier + ema_list[-1])
    return ema_list

# --- الفئة الهجينة المتقدمة (Ultimate Hybrid Bot Core) ---
class UltimateHybridBot:
    def __init__(self, initial_capital=10000.0):
        self.capital = initial_capital
        self.scalping_allocation = 0.40  
        self.rebalance_allocation = 0.60 
        self.target_weights = {"USDT": 0.50, "BTC": 0.25, "ETH": 0.25}
        self.current_portfolio = {"USDT": 3000.0, "BTC": 2500.0, "ETH": 4500.0}
        self.base_drift_threshold = 0.05  
        self.bull_run_drift_threshold = 0.20 

    def analyze_market_liquidity_and_trend(self, market_data):
        volume = market_data.get('volume', 0)
        mean_volume = market_data.get('mean_volume_20', 1)
        close_price = market_data.get('close', 0)
        ema_200 = market_data.get('ema_200', 0)

        liquidity_score = volume / mean_volume if mean_volume > 0 else 1.0
        high_liquidity = liquidity_score > 1.5  
        is_bull_market = (close_price > ema_200) and high_liquidity

        return {
            "liquidity_score": liquidity_score,
            "high_liquidity": high_liquidity,
            "is_bull_market": is_bull_market
        }

    def smart_scalp_engine(self, current_price, price_history, market_status):
        if len(price_history) < 10 or not market_status['high_liquidity']:
            return "HOLD", "سيولة منخفضة - تجنب التداول"

        sma_5 = sum(price_history[-5:]) / 5
        
        if current_price > sma_5 * 1.001 and market_status['liquidity_score'] > 1.2:
            return "BUY", "إشارة شراء سكالبينغ مؤكدة بالسيولة والزخم"
        elif current_price < sma_5 * 0.998:
            return "SELL", "إشارة خروج / جني أرباح سريعة"
            
        return "HOLD", "استقرار في النطاق"

hybrid_bot = UltimateHybridBot()

async def execute_okx_order(session, symbol, side, sz):
    url = "https://www.okx.com/api/v5/trade/order"
    body_dict = {
        "instId": symbol,
        "tdMode": "cash",
        "side": side,
        "ordType": "market",
        "sz": str(sz)
    }
    body_str = json.dumps(body_dict)
    headers = get_okx_headers("POST", "/api/v5/trade/order", body_str)
    try:
        async with session.post(url, headers=headers, data=body_str, timeout=10) as resp:
            if resp.status == 200:
                res_data = await resp.json()
                if res_data.get("code") == "0":
                    print(f"[{get_riyadh_time()}] 🛒 تنفيذ طلب {side} لـ {symbol} بنجاح", flush=True, file=sys.stdout)
                    return True, res_data
    except Exception as e:
        print(f"[{get_riyadh_time()}] ❌ خطأ تنفيذ الطلب: {e}", flush=True, file=sys.stdout)
    return False, None

# --- حلقة البصمة أو الإنعاش الذاتي (Self-Ping Keep-Alive) ---
async def self_ping_loop():
    port = int(os.environ.get("PORT", 10000))
    render_url = f"http://127.0.0.1:{port}/"
    while True:
        try:
            await asyncio.sleep(120)
            async with aiohttp.ClientSession() as session:
                async with session.get(render_url) as resp:
                    pass
        except Exception:
            pass

# --- الحلقة التشغيلية المدمجة في الخلفية ---
async def background_hybrid_bot_supervisor():
    print(f"[{get_riyadh_time()}] 🤖 [تشغيل مستمر] نظام البوت الهجيد المستقر يعمل الآن...", flush=True, file=sys.stdout)
    active_position = None

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                if not BOT_SETTINGS["is_running"]:
                    await asyncio.sleep(10)
                    continue

                if active_position and active_position["in_pos"]:
                    symbol = active_position["symbol"]
                    candles = await fetch_okx_candles(session, symbol)
                    if candles:
                        curr_price = candles[-1]['close']
                        entry = active_position["entry_price"]
                        amount = active_position["amount"]
                        profit_ratio = (curr_price - entry) / entry

                        if curr_price > active_position["highest_price"]:
                            active_position["highest_price"] = curr_price

                        elapsed_minutes = (datetime.now(RIYADH_TZ) - active_position["entry_time"]).total_seconds() / 60
                        drop_from_peak = (active_position["highest_price"] - curr_price) / active_position["highest_price"]

                        print(f"[{get_riyadh_time()}] 🛡️ [متابعة صفقة] {symbol} | الربح: {profit_ratio*100:.2f}%", flush=True, file=sys.stdout)

                        if profit_ratio >= BOT_SETTINGS["target_tp_pct"] or ((profit_ratio >= 0.005) and (drop_from_peak >= 0.004)) or profit_ratio <= -BOT_SETTINGS["stop_loss_pct"] or elapsed_minutes >= 45:
                            print(f"[{get_riyadh_time()}] 🔴 [إغلاق صفقة] بيع {symbol} عند السعر {curr_price}", flush=True, file=sys.stdout)
                            await execute_okx_order(session, symbol, "sell", amount)
                            
                            profit_usdt = (curr_price - entry) * (amount * curr_price)
                            active_position = None
                            BOT_STATS["total_trades"] += 1
                            if profit_usdt > 0:
                                BOT_STATS["winning_trades"] += 1
                            else:
                                BOT_STATS["losing_trades"] += 1
                            BOT_STATS["total_profit_usdt"] += profit_usdt
                            BOT_STATS["active_positions_count"] = 0

                    await asyncio.sleep(10)
                    continue

                for symbol in MAJOR_COINS:
                    if symbol in BOT_SETTINGS["excluded_symbols"]:
                        continue

                    candles = await fetch_okx_candles(session, symbol)
                    if not candles or len(candles) < 205:
                        continue

                    df = pd.DataFrame(candles)
                    closes = df['close'].tolist()
                    volumes = df['volume'].tolist()
                    
                    ema_200_list = calculate_ema(closes, 200)
                    mean_vol_20 = sum(volumes[-20:]) / 20

                    market_data = {
                        'close': closes[-1],
                        'mean_volume_20': mean_vol_20,
                        'volume': volumes[-1],
                        'ema_200': ema_200_list[-1]
                    }

                    market_status = hybrid_bot.analyze_market_liquidity_and_trend(market_data)
                    price_history = closes[-15:]
                    
                    scalp_action, scalp_msg = hybrid_bot.smart_scalp_engine(market_data['close'], price_history, market_status)
                    print(f"[{get_riyadh_time()}] 📊 [{symbol}] | السعر: {market_data['close']} | الحالة: {scalp_msg}", flush=True, file=sys.stdout)

                    if scalp_action == "BUY":
                        synchronized_price = market_data['close']
                        sz_amount = round(BOT_SETTINGS["trade_amount_usdt"] / synchronized_price, 4)

                        print("="*50, flush=True, file=sys.stdout)
                        print(f"🚀 [إشارة دخول هجينة] عملة: {symbol} | السعر: {synchronized_price}", flush=True, file=sys.stdout)
                        print("="*50, flush=True, file=sys.stdout)

                        success, _ = await execute_okx_order(session, symbol, "buy", sz_amount)
                        if success:
                            active_position = {
                                "in_pos": True,
                                "symbol": symbol,
                                "entry_price": synchronized_price,
                                "highest_price": synchronized_price,
                                "amount": sz_amount,
                                "entry_time": datetime.now(RIYADH_TZ)
                            }
                            BOT_STATS["active_positions_count"] = 1
                            break

            except Exception as e:
                print(f"[{get_riyadh_time()}] ⚠️ خطأ في دورة التشغيل: {e}", flush=True, file=sys.stdout)

            await asyncio.sleep(15)

async def main():
    await asyncio.gather(
        self_ping_loop(),
        background_hybrid_bot_supervisor()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

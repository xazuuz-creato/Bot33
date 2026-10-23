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
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

# --- إعدادات التوقيت المحلي لمدينة الرياض ---
RIYADH_TZ = timezone(timedelta(hours=3))

def get_riyadh_time():
    return datetime.now(RIYADH_TZ).strftime('%Y-%m-%d %I:%M:%S %p')

class BotConfig(BaseModel):
    is_running: bool = True
    trade_amount_usdt: float = 100.0
    target_tp_pct: float = 0.03
    stop_loss_pct: float = 0.007
    excluded_symbols: list = []

app = FastAPI(title="Ultimate Hybrid OKX Bot", version="7.0")

BOT_STATE = {
    "config": BotConfig(),
    "stats": {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_profit_usdt": 0.0,
        "active_positions_count": 0,
        "engine_logs": {}
    },
    "active_position": None
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

    def dynamic_rebalancing(self, market_status):
        active_drift_limit = self.bull_run_drift_threshold if market_status['is_bull_market'] else self.base_drift_threshold
        total_value = sum(self.current_portfolio.values())
        actions = []

        for asset, target_weight in self.target_weights.items():
            current_value = self.current_portfolio.get(asset, 0)
            actual_weight = current_value / total_value
            drift = actual_weight - target_weight

            if abs(drift) > active_drift_limit:
                if drift > 0:
                    if market_status['is_bull_market'] and drift < self.bull_run_drift_threshold:
                        actions.append(f"[{asset}] تجاوز النطاق العادي ولكننا في سوق صاعد: الاحتفاظ بالأصل لتفادي تكلفة الفرصة البديلة.")
                    else:
                        actions.append(f"إعادة توازن: بيع فائض {asset} لتخفيض الوزن.")
                else:
                    actions.append(f"إعادة توازن: شراء وتعزيز {asset} لانخفاض وزنه.")
            else:
                actions.append(f"[{asset}] المحفظة متوازنة ضمن النطاق الآمن.")

        return actions

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

# --- نظام الحفاظ على استمرار السيرفر (Self-Ping Keep-Alive) لمنع خمول Render ---
async def self_ping_keep_alive():
    await asyncio.sleep(10)
    port = os.environ.get("PORT", "10000")
    url = f"http://127.0.0.1:{port}/"
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url, timeout=5) as resp:
                    pass
            except Exception:
                pass
            await asyncio.sleep(25) # نبضة دورية كل 25 ثانية لإبقاء السيرفر نشطاً

# --- الحلقة التشغيلية المدمجة في الخلفية ---
async def background_hybrid_bot_supervisor():
    print(f"[{get_riyadh_time()}] 🤖 [تشغيل مستمر] نظام البوت الهجيد (Ultimate Hybrid Bot) يعمل الآن...", flush=True, file=sys.stdout)
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                config = BOT_STATE["config"]
                if not config.is_running:
                    await asyncio.sleep(10)
                    continue

                active_pos = BOT_STATE["active_position"]
                if active_pos and active_pos["in_pos"]:
                    symbol = active_pos["symbol"]
                    candles = await fetch_okx_candles(session, symbol)
                    if candles:
                        curr_price = candles[-1]['close']
                        entry = active_pos["entry_price"]
                        amount = active_pos["amount"]
                        profit_ratio = (curr_price - entry) / entry

                        if curr_price > active_pos["highest_price"]:
                            active_pos["highest_price"] = curr_price

                        elapsed_minutes = (datetime.now(RIYADH_TZ) - active_pos["entry_time"]).total_seconds() / 60
                        drop_from_peak = (active_pos["highest_price"] - curr_price) / active_pos["highest_price"]

                        print(f"[{get_riyadh_time()}] 🛡️ [متابعة صفقة] {symbol} | الربح: {profit_ratio*100:.2f}%", flush=True, file=sys.stdout)

                        if profit_ratio >= config.target_tp_pct or ((profit_ratio >= 0.005) and (drop_from_peak >= 0.004)) or profit_ratio <= -config.stop_loss_pct or elapsed_minutes >= 45:
                            print(f"[{get_riyadh_time()}] 🔴 [إغلاق صفقة] بيع {symbol} عند السعر {curr_price}", flush=True, file=sys.stdout)
                            await execute_okx_order(session, symbol, "sell", amount)
                            
                            profit_usdt = (curr_price - entry) * (amount * curr_price)
                            BOT_STATE["active_position"] = None
                            BOT_STATE["stats"]["total_trades"] += 1
                            if profit_usdt > 0:
                                BOT_STATE["stats"]["winning_trades"] += 1
                            else:
                                BOT_STATE["stats"]["losing_trades"] += 1
                            BOT_STATE["stats"]["total_profit_usdt"] += profit_usdt
                            BOT_STATE["stats"]["active_positions_count"] = 0

                    await asyncio.sleep(10)
                    continue

                for symbol in MAJOR_COINS:
                    if symbol in config.excluded_symbols:
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
                    BOT_STATE["stats"]["engine_logs"][symbol] = f"{scalp_action} | {scalp_msg}"

                    print(f"[{get_riyadh_time()}] 📊 [{symbol}] | السعر: {market_data['close']} | الإشارة: {scalp_action}", flush=True, file=sys.stdout)

                    if scalp_action == "BUY":
                        synchronized_price = market_data['close']
                        sz_amount = round(config.trade_amount_usdt / synchronized_price, 4)

                        print("="*50, flush=True, file=sys.stdout)
                        print(f"🚀 [إشارة دخول هجينة] عملة: {symbol} | السعر: {synchronized_price}", flush=True, file=sys.stdout)
                        print("="*50, flush=True, file=sys.stdout)

                        success, _ = await execute_okx_order(session, symbol, "buy", sz_amount)
                        if success:
                            BOT_STATE["active_position"] = {
                                "in_pos": True,
                                "symbol": symbol,
                                "entry_price": synchronized_price,
                                "highest_price": synchronized_price,
                                "amount": sz_amount,
                                "entry_time": datetime.now(RIYADH_TZ)
                            }
                            BOT_STATE["stats"]["active_positions_count"] = 1
                            break

            except Exception as e:
                print(f"[{get_riyadh_time()}] ⚠️ خطأ في دورة التشغيل: {e}", flush=True, file=sys.stdout)

            await asyncio.sleep(15)

@app.get("/")
def health_check():
    return {"status": "ok", "system": "Ultimate Hybrid Bot Running Continuously"}

@app.get("/api/status")
def get_status():
    return BOT_STATE

@app.post("/api/toggle")
def toggle_bot():
    BOT_STATE["config"].is_running = not BOT_STATE["config"].is_running
    return {"success": True, "is_running": BOT_STATE["config"].is_running}

@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_event_loop()
    loop.create_task(background_hybrid_bot_supervisor())
    loop.create_task(self_ping_keep_alive()) # تفعيل مانع الخمول

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("bot:app", host="0.0.0.0", port=port)

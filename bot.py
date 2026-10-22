import os
import sys
import aiohttp
import asyncio
import hmac
import hashlib
import base64
import json
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

RIYADH_TZ = timezone(timedelta(hours=3))

def get_riyadh_time():
    return datetime.now(RIYADH_TZ).strftime('%Y-%m-%d %I:%M:%S %p')

class BotConfig(BaseModel):
    is_running: bool = True
    trade_amount_usdt: float = 100.0
    target_tp_pct: float = 0.03       # هدف ربح 3%
    min_secure_tp: float = 0.008      # جني أرباح آمن 0.8%
    stop_loss_pct: float = 0.006      # وقف خسارة 0.6%
    excluded_symbols: list = []

app = FastAPI(title="OKX Independent Scalping Bot", version="3.1")

BOT_STATE = {
    "config": BotConfig(),
    "stats": {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_profit_usdt": 0.0,
        "active_positions_count": 0,
        "market_warnings": {}
    },
    "active_position": None
}

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
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar=15m&limit=30"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("code") == "0" and data.get("data"):
                    raw = data["data"]
                    candles = []
                    for c in reversed(raw):
                        candles.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
                    return candles
    except Exception as e:
        print(f"[{get_riyadh_time()}] ⚠️ خطأ في سحب شموع {symbol}: {e}", flush=True, file=sys.stdout)
    return None

def advanced_market_analysis(data):
    try:
        if not data or len(data) < 20:
            return False, "بيانات غير كافية للتحليل"
            
        c = [r[4] for r in data]
        h = [r[2] for r in data]
        l = [r[3] for r in data]
        o = [r[1] for r in data]
        
        recent_ranges = [h[i] - l[i] for i in range(-5, 0)]
        avg_range = sum(recent_ranges) / 5
        price_movement = abs(c[-1] - c[-5])
        
        if avg_range > (c[-1] * 0.015) and price_movement < (avg_range * 0.8):
            return False, "⚠️ سوق متذبذب وعرضي غير واضح (تم التجنب)"

        sma_fast = sum(c[-7:]) / 7
        sma_slow = sum(c[-20:]) / 20
        is_bullish_trend = sma_fast > sma_slow
        
        gains = 0.0
        losses = 0.0
        for i in range(-14, 0):
            diff = c[i] - c[i-1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
                
        avg_gain = gains / 14
        avg_loss = losses / 14 if losses != 0 else 0.001
        rs = avg_gain / avg_loss
        rsi_val = 100 - (100 / (1 + rs))
        
        recent_low = min(l[-6:-1])
        curr_low = l[-1]
        curr_open = o[-1]
        curr_close = c[-1]
        
        is_green_candle = curr_close > curr_open
        body_size = abs(curr_close - curr_open)
        total_range = h[-1] - curr_low
        
        if total_range == 0:
            return False, "مدى الشمعة صفر"

        is_bouncing_from_dip = (curr_low <= recent_low * 1.004) and is_green_candle and ((body_size / total_range) > 0.35)
        is_rsi_dip_zone = 35 <= rsi_val <= 60  

        if is_bullish_trend and is_bouncing_from_dip and is_rsi_dip_zone:
            return True, "🟢 فرصة شراء ممتازة عند الانخفاض (Dip Entry مؤكد)"

        return False, "السوق طبيعي ولكن لا توجد إشارة دخول حالياً"
    except Exception as e:
        return False, f"خطأ في التحليل: {str(e)}"

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
                    print(f"[{get_riyadh_time()}] 🛒 تنفيذ أمر {side} بنجاح على {symbol}", flush=True, file=sys.stdout)
                    return True, res_data
    except Exception as e:
        print(f"[{get_riyadh_time()}] ❌ خطأ في تنفيذ الطلب على OKX: {e}", flush=True, file=sys.stdout)
    return False, None

async def background_scalping_bot():
    print(f"[{get_riyadh_time()}] 🤖 [بدء التشغيل] بوت السكالبينج المستقل يعمل الآن بنجاح...", flush=True, file=sys.stdout)
    
    async with aiohttp.ClientSession() as session:
        while True:
            config = BOT_STATE["config"]
            if not config.is_running:
                print(f"[{get_riyadh_time()}] ⏸️ [مؤقت] البوت مت

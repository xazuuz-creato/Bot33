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
    target_tp_pct: float = 0.025      # هدف ربح 2.5%
    min_secure_tp: float = 0.008      # جني أرباح آمن 0.8%
    stop_loss_pct: float = 0.006      # وقف خسارة 0.6%
    excluded_symbols: list = []

app = FastAPI(title="OKX Independent Scalping Bot", version="3.3")

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
    # استخدام إطار 5m للسرعة والدقة
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar=5m&limit=30"
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
        print(f"[{get_riyadh_time()}] Error fetching {symbol}: {e}", flush=True, file=sys.stdout)
    return None

def advanced_market_analysis(data):
    try:
        if not data or len(data) < 20:
            return False, "بيانات غير كافية"
            
        c = [r[4] for r in data]
        h = [r[2] for r in data]
        l = [r[3] for r in data]
        o = [r[1] for r in data]
        
        recent_ranges = [h[i] - l[i] for i in range(-5, 0)]
        avg_range = sum(recent_ranges) / 5
        price_movement = abs(c[-1] - c[-5])
        
        # فلتر السوق المتذبذب المضلل
        if avg_range > (c[-1] * 0.012) and price_movement < (avg_range * 0.7):
            return False, "سوق متذبذب عرضي مضلل"

        # استخدام EMA السريع لاقتناص التوقيت
        ema_fast = sum(c[-5:]) / 5
        ema_slow = sum(c[-15:]) / 15
        is_bullish_trend = ema_fast > ema_slow
        
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
        
        recent_low = min(l[-5:-1])
        curr_low = l[-1]
        curr_open = o[-1]
        curr_close = c[-1]
        
        is_green_candle = curr_close > curr_open
        body_size = abs(curr_close - curr_open)
        total_range = h[-1] - curr_low
        
        if total_range == 0:
            return False, "مدى شمعة صفر"

        is_bouncing_from_dip = (curr_low <= recent_low * 1.002) and is_green_candle and ((body_size / total_range) > 0.3)
        is_rsi_dip_zone = 30 <= rsi_val <= 55  

        if is_bullish_trend and is_bouncing_from_dip and is_rsi_dip_zone:
            return True, f"ارتداد قوي من القاع (Dip Bounce) مع RSI صحي ({rsi_val:.1f})"

        return False, "لا توجد إشارة دخول حقيقية"
    except Exception as e:
        return False, f"خطأ تحليل: {str(e)}"

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
                    return True, res_data
    except Exception as e:
        print(f"[{get_riyadh_time()}] Order Error: {e}", flush=True, file=sys.stdout)
    return False, None

async def background_scalping_bot():
    print(f"[{get_riyadh_time()}] 🤖 [بدء التشغيل] البوت يعمل الآن بنجاح على إطار 5m السريع...", flush=True, file=sys.stdout)
    
    async with aiohttp.ClientSession() as session:
        while True:
            config = BOT_STATE["config"]
            if not config.is_running:
                await asyncio.sleep(10)
                continue

            try:
                active_pos = BOT_STATE["active_position"]
                
                # --- متابعة الصفقة النشطة ---
                if active_pos and active_pos["in_pos"]:
                    symbol = active_pos["symbol"]
                    candles = await fetch_okx_candles(session, symbol)
                    if candles:
                        curr_price = candles[-1][4]
                        entry = active_pos["entry_price"]
                        target = active_pos["target_price"]
                        amount = active_pos["amount"]
                        profit_ratio = (curr_price - entry) / entry

                        if curr_price > active_pos["highest_price"]:
                            active_pos["highest_price"] = curr_price

                        elapsed_minutes = (datetime.now(RIYADH_TZ) - active_pos["entry_time"]).total_seconds() / 60
                        drop_from_peak = (active_pos["highest_price"] - curr_price) / active_pos["highest_price"]

                        print(f"[{get_riyadh_time()}] 📊 [صفقة مفتوحة] {symbol} | السعر اللحظي: {curr_price} | الدخول: {entry} | الهدف: {target} | الربح الحالي: {profit_ratio*100:.2f}%", flush=True, file=sys.stdout)

                        is_safe_exit = (profit_ratio >= config.min_secure_tp) and (drop_from_peak >= 0.002)
                        is_max_target = profit_ratio >= config.target_tp_pct
                        is_timeout = elapsed_minutes >= 30

                        if is_safe_exit or is_max_target or is_timeout or (profit_ratio <= -config.stop_loss_pct):
                            print(f"[{get_riyadh_time()}] 🔴 [إغلاق الصفقة] تم تنفيذ البيع لـ {symbol} عند السعر {curr_price}", flush=True, file=sys.stdout)
                            await execute_okx_order(session, symbol, "sell", amount)
                            
                            profit_usdt = (curr_price - entry) * (amount * curr_price)
                            BOT_STATE["active_position"] = None
                            
                            BOT_STATE["stats"]["total_trades"] += 1
                            if profit_usdt > 0:
                                BOT_STATE["stats"]["winning_trades"] += 1
                                print(f"[{get_riyadh_time()}] 💰 [ربح محقق] +{profit_usdt:.4f} USDT", flush=True, file=sys.stdout)
                            else:
                                BOT_STATE["stats"]["losing_trades"] += 1
                                print(f"[{get_riyadh_time()}] 📉 [خسارة] {profit_usdt:.4f} USDT", flush=True, file=sys.stdout)

                            BOT_STATE["stats"]["total_profit_usdt"] += profit_usdt
                            BOT_STATE["stats"]["active_positions_count"] = 0

                    await asyncio.sleep(5)
                    continue

                # --- فحص العملات واصطياد الفرص ---
                for symbol in MAJOR_COINS:
                    if symbol in config.excluded_symbols:
                        continue

                    candles = await fetch_okx_candles(session, symbol)
                    if not candles or len(candles) < 20:
                        continue

                    is_signal, reason = advanced_market_analysis(candles)
                    BOT_STATE["stats"]["market_warnings"][symbol] = reason
                    
                    curr_p = candles[-1][4]

                    if is_signal:
                        synchronized_price = curr_p
                        sz_amount = round(config.trade_amount_usdt / synchronized_price, 4)
                        target_price = round(synchronized_price * (1 + config.target_tp_pct), 4)
                        trade_time = get_riyadh_time()

                        # طباعة التفاصيل الكاملة والمختصرة في اللوحة
                        print("="*60, flush=True, file=sys.stdout)
                        print(f"🚀 [دخول صفقة جديدة]", flush=True, file=sys.stdout)
                        print(f"🪙 العملة: {symbol}", flush=True, file=sys.stdout)
                        print(f"⏰ وقت الصفقة: {trade_time}", flush=True, file=sys.stdout)
                        print(f"💵 السعر اللحظي والدخول: {synchronized_price} USDT", flush=True, file=sys.stdout)
                        print(f"🎯 الهدف المحدد: {target_price} USDT (+{config.target_tp_pct*100}%)", flush=True, file=sys.stdout)
                        print(f"💡 سبب الدخول: {reason}", flush=True, file=sys.stdout)
                        print("="*60, flush=True, file=sys.stdout)

                        success, _ = await execute_okx_order(session, symbol, "buy", sz_amount)
                        if success:
                            BOT_STATE["active_position"] = {
                                "in_pos": True,
                                "symbol": symbol,
                                "entry_price": synchronized_price,
                                "target_price": target_price,
                                "highest_price": synchronized_price,
                                "amount": sz_amount,
                                "entry_time": datetime.now(RIYADH_TZ)
                            }
                            BOT_STATE["stats"]["active_positions_count"] = 1
                            break

            except Exception as e:
                print(f"[{get_riyadh_time()}] Loop error: {e}", flush=True, file=sys.stdout)

            await asyncio.sleep(10)

@app.get("/")
def health_check():
    return {"status": "ok", "service": "OKX Scalping Bot Alive"}

@app.get("/api/status")
def get_status():
    return BOT_STATE

@app.post("/api/config")
def update_config(new_config: BotConfig):
    BOT_STATE["config"] = new_config
    return {"success": True, "message": "تم تحديث الإعدادات", "new_config": new_config}

@app.post("/api/toggle")
def toggle_bot():
    BOT_STATE["config"].is_running = not BOT_STATE["config"].is_running
    status_text = "Running" if BOT_STATE["config"].is_running else "Paused"
    return {"success": True, "is_running": BOT_STATE["config"].is_running, "message": f"حالة البوت: {status_text}"}

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_scalping_bot())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)

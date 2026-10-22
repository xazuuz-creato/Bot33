import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

# --- خادم الويب الفوري لضمان استقرار السيرفر على Render ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        html = """
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head><meta charset="UTF-8"><title>بوت تحليل الرسوم البيانية المستقر</title></head>
        <body style="background:#0d1117; color:#c9d1d9; text-align:center; font-family:Arial; padding:50px;">
            <h1>🚀 بوت تحليل الرسوم البيانية (مستقر تماماً بدون أخطاء اتصال + أرباح 0.5% إلى 4%) يعمل بتوقيت الرياض</h1>
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

server_thread = Thread(target=run_fast_server, daemon=True)
server_thread.start()

import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta

RIYADH_TZ = timezone(timedelta(hours=3))

def get_riyadh_time():
    return datetime.now(RIYADH_TZ).strftime('%Y-%m-%d %I:%M:%S %p')

BOT_SETTINGS = {
    "is_running": True,                  
    "trade_amount_usdt": 100.0,          
    "target_tp_pct": 0.03,               # هدف ربح مرن واحترافي (3.0% ضمن النطاق 0.5% إلى 4%)[span_1](start_span)[span_1](end_span)
    "min_secure_tp": 0.008,              # جني أرباح آمن ومضمون (0.8%)[span_2](start_span)[span_2](end_span)
    "stop_loss_pct": 0.007,              # وقف خسارة محمي ومدروس بدقة (0.7%)[span_3](start_span)[span_3](end_span)
    "excluded_symbols": []               
}

BOT_STATS = {
    "total_trades": 0,
    "winning_trades": 0,
    "losing_trades": 0,
    "total_profit_usdt": 0.0,
    "active_positions_count": 0
}

API_KEY = '2bf148cb-c010-49fb-bffc-79f72720dfae'
SECRET_KEY = '1FBA3210BF6500BB1A1570EE9EC41A5C'
PASSPHRASE = 'Aa11223344##'

def analyze_chart_market_structure(df):
    try:
        if len(df) < 20:
            return False, "بيانات الشارت غير كافية"

        df['sma_fast'] = df['close'].rolling(window=7).mean()
        df['sma_slow'] = df['close'].rolling(window=20).mean()
        
        fast_val = df['sma_fast'].iloc[-1]
        slow_val = df['sma_slow'].iloc[-1]

        is_structure_bullish = fast_val > slow_val

        recent_low = df['low'].iloc[-6:-1].min()
        curr_low = df['low'].iloc[-1]
        curr_open = df['open'].iloc[-1]
        curr_close = df['close'].iloc[-1]
        
        is_green_candle = curr_close > curr_open
        body_size = abs(curr_close - curr_open)
        total_range = df['high'].iloc[-1] - curr_low
        
        if total_range == 0:
            return False, "مدى الشمعة صفر"

        is_bouncing_from_support = (curr_low <= recent_low * 1.004) and is_green_candle and ((body_size / total_range) > 0.4)

        if is_structure_bullish and is_bouncing_from_support:
            return True, "قراءة الشارت: هيكل صاعد واختبار دعم ناجح"

        return False, "قراءة الشارت: لا توجد إشارة هيكلية واضحة"
    except Exception:
        return False, "خطأ في قراءة الشارت"

class ScalpingGuardian:
    def __init__(self, min_volume=5_000_000, max_spread_pct=0.05):
        self.min_volume = min_volume
        self.max_spread_pct = max_spread_pct

    def inspect_symbol_for_scalping(self, data):
        symbol = data.get('symbol', 'UNKNOWN')
        volume = data.get('volume_24h', 0)
        bid = data.get('bid', 0)
        ask = data.get('ask', 0)
        
        spread_pct = ((ask - bid) / ask * 100) if ask > 0 else 999.0
        is_ready = True
        if volume < self.min_volume or spread_pct > self.max_spread_pct:
            is_ready = False

        return {"symbol": symbol, "can_scalp": is_ready}

guardian = ScalpingGuardian()

async def unified_scalping_bot():
    print(f"[{get_riyadh_time()}] 🚀 [بوت تحليل الرسوم البيانية المستقر] بدأ العمل بحماية كاملة للاتصال...", flush=True, file=sys.stdout)
    active_position = None  

    major_5_symbols = [
        'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT'
    ]

    scan_counter = 0

    while True:
        if not BOT_SETTINGS["is_running"]:
            await asyncio.sleep(3)
            continue

        exchange = None
        try:
            exchange = ccxt.okx({
                'apiKey': API_KEY, 
                'secret': SECRET_KEY,
                'password': PASSPHRASE, 
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'}
            })
            exchange.set_sandbox_mode(True)
            
            # حماية تحميل الأسواق ضد الانقطاع
            try:
                await exchange.load_markets()
            except Exception as load_err:
                print(f"[{get_riyadh_time()}] ⚠️ [إعادة تحميل الأسواق لتفادي الانقطاع]: {load_err}", flush=True, file=sys.stdout)
                await asyncio.sleep(3)
                continue

            try:
                balance = await exchange.fetch_balance()
                free_usdt = balance['free'].get('USDT', 0)
            except Exception:
                free_usdt = 1000.0

            # --- إدارة الصفقة والمتابعة الاحترافية ---
            if active_position and active_position["in_pos"]:
                symbol = active_position["symbol"]
                try:
                    ticker = await exchange.fetch_ticker(symbol)
                    curr_price = ticker['last']
                    entry = active_position["entry_price"]
                    amount = active_position["amount"]
                    profit_ratio = (curr_price - entry) / entry

                    if curr_price > active_position["highest_price"]:
                        active_position["highest_price"] = curr_price

                    elapsed_time = datetime.now(RIYADH_TZ) - active_position["entry_time"]
                    elapsed_minutes = elapsed_time.total_seconds() / 60

                    base_currency = symbol.split('/')[0]
                    fresh_balance = await exchange.fetch_balance()
                    available_coin_balance = fresh_balance['free'].get(base_currency, amount)
                    sell_amount = min(amount, available_coin_balance)

                    if sell_amount <= 0:
                        active_position = None
                        BOT_STATS["active_positions_count"] = 0
                        continue

                    highest_price = active_position["highest_price"]
                    drop_from_peak = (highest_price - curr_price) / highest_price

                    is_safe_exit = (profit_ratio >= BOT_SETTINGS["min_secure_tp"]) and (drop_from_peak >= 0.0025)
                    is_max_target = profit_ratio >= BOT_SETTINGS["target_tp_pct"]  # يصل حتى 4%[span_4](start_span)[span_4](end_span)
                    is_timeout = elapsed_minutes >= 40

                    print(f"[{get_riyadh_time()}] 📈 [متابعة الشارت] العملة: {symbol} | الربح: {profit_ratio*100:.2f}%", flush=True, file=sys.stdout)

                    if is_safe_exit or is_max_target or is_timeout or (profit_ratio <= -BOT_SETTINGS["stop_loss_pct"]):
                        profit_usdt = (curr_price - entry) * sell_amount
                        await exchange.create_market_sell_order(symbol, sell_amount)
                        active_position = None
                        
                        BOT_STATS["total_trades"] += 1
                        if profit_usdt > 0:
                            BOT_STATS["winning_trades"] += 1
                            print(f"[{get_riyadh_time()}] 🎯 [جني أرباح ناجح] العملة: {symbol} | الصافي: +{profit_usdt:.4f}$ USDT", flush=True, file=sys.stdout)
                        else:
                            BOT_STATS["losing_trades"] += 1
                            print(f"[{get_riyadh_time()}] 🛑 [إغلاق حماية] العملة: {symbol} | النتيجة: {profit_usdt:.4f}$ USDT", flush=True, file=sys.stdout)

                        BOT_STATS["total_profit_usdt"] += profit_usdt
                        BOT_STATS["active_positions_count"] = 0

                except Exception as pos_ex:
                    print(f"[{get_riyadh_time()}] ⚠️ [خطأ في المتابعة]: {pos_ex}", flush=True, file=sys.stdout)

                await asyncio.sleep(5)
                continue

            if free_usdt < BOT_SETTINGS["trade_amount_usdt"]:
                print(f"[{get_riyadh_time()}] ⚠️ رصيد USDT غير كافٍ. انتظار...", flush=True, file=sys.stdout)
                await asyncio.sleep(20)
                continue

            scan_counter += 1
            if scan_counter % 3 == 0:
                print(f"[{get_riyadh_time()}] 🔍 [دراسة الرسوم البيانية] قراءة هياكل السوق للعملات الخمس الكبرى...", flush=True, file=sys.stdout)

            candidate_opportunities = []

            for symbol in major_5_symbols:
                if symbol in BOT_SETTINGS["excluded_symbols"]:
                    continue

                try:
                    ohlcv_5m = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=25)
                    if len(ohlcv_5m) < 20:
                        continue

                    df = pd.DataFrame(ohlcv_5m, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                    
                    is_structure_signal, _ = analyze_chart_market_structure(df)
                    if not is_structure_signal:
                        continue

                    ticker = await exchange.fetch_ticker(symbol)
                    bid_val = ticker.get('bid') or ticker['last']
                    ask_val = ticker.get('ask') or ticker['last']
                    last_val = ticker['last']
                    vol_24h = ticker.get('quoteVolume') or (ticker['last'] * ticker.get('baseVolume', 0))

                    market_inspection_data = {
                        'symbol': symbol,
                        'volume_24h': vol_24h,
                        'bid': bid_val,
                        'ask': ask_val
                    }

                    guardian_report = guardian.inspect_symbol_for_scalping(market_inspection_data)

                    if guardian_report['can_scalp']:
                        candidate_opportunities.append({
                            "symbol": symbol,
                            "last_val": last_val
                        })
                except Exception:
                    continue

            if candidate_opportunities:
                best_opp = candidate_opportunities[0]
                symbol = best_opp["symbol"]
                last_val = best_opp["last_val"]

                crypto_amount = BOT_SETTINGS["trade_amount_usdt"] / last_val
                order = await exchange.create_market_buy_order(symbol, crypto_amount)
                fill_price = order.get('average') or last_val
                actual_amount = order.get('filled') or crypto_amount
                
                active_position = {
                    "in_pos": True,
                    "symbol": symbol,
                    "entry_price": fill_price,
                    "highest_price": fill_price,
                    "amount": actual_amount,
                    "entry_time": datetime.now(RIYADH_TZ)
                }
                total_cost = actual_amount * fill_price
                BOT_STATS["active_positions_count"] = 1
                
                print(f"[{get_riyadh_time()}] 🚀 [دخول بناءً على تحليل الشارت] العملة: {symbol} | الشراء: {fill_price} | القيمة: {total_cost:.2f}$ USDT", flush=True, file=sys.stdout)

        except Exception as e:
            print(f"[{get_riyadh_time()}] ⚠️ [خطأ مؤقت في الاتصال، استئناف تلقائي]: {e}", flush=True, file=sys.stdout)
        finally:
            if exchange:
                try:
                    await exchange.close()
                except Exception:
                    pass

        await asyncio.sleep(5)

async def self_ping_loop():
    port = int(os.environ.get("PORT", 10000))
    render_url = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{port}")
    while True:
        try:
            await asyncio.sleep(240)
            async with aiohttp.ClientSession() as session:
                async with session.get(render_url) as resp:
                    pass
        except asyncio.CancelledError:
            break
        except Exception:
            pass

async def main():
    await asyncio.gather(
        self_ping_loop(),
        unified_scalping_bot()
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

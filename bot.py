import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import os
import aiohttp
from aiohttp import web

# --- متغيرات تتبع الأرباح والصفقات العامة ---
BOT_STATS = {
    "total_trades": 0,
    "winning_trades": 0,
    "losing_trades": 0,
    "total_profit_usdt": 0.0,
    "total_profit_pct": 0.0,
    "active_positions_count": 0
}

# --- خادم ويب لوحة التحكم ---
async def handle(request):
    html_content = f"""
    <!DOCTYPE html>
    <html dir="rtl" lang="ar">
    <head>
        <meta charset="UTF-8">
        <title>لوحة تحكم بوت السكالبينغ الذكي</title>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #0d1117; color: #c9d1d9; text-align: center; padding: 20px; }}
            .card {{ background-color: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 20px; margin: 15px auto; max-width: 500px; }}
            h1 {{ color: #58a6ff; }}
            .stat {{ font-size: 20px; margin: 10px 0; }}
            .profit {{ color: #3fb950; font-weight: bold; }}
            .loss {{ color: #f85149; font-weight: bold; }}
        </style>
    </head>
    <body>
        <h1>🚀 لوحة تحكم بوت السكالبينغ (10 USDT لكل صفقة)</h1>
        <div class="card">
            <div class="stat">إجمالي الصفقات المنفذة: <b>{BOT_STATS['total_trades']}</b></div>
            <div class="stat">الصفقات الناجحة: <span class="profit">{BOT_STATS['winning_trades']}</span> | الخاسرة: <span class="loss">{BOT_STATS['losing_trades']}</span></div>
            <div class="stat">إجمالي الأرباح (USDT): <span class="{'profit' if BOT_STATS['total_profit_usdt'] >= 0 else 'loss'}">${BOT_STATS['total_profit_usdt']:.4f}</span></div>
            <div class="stat">نسبة الأرباح الإجمالية: <span class="{'profit' if BOT_STATS['total_profit_pct'] >= 0 else 'loss'}">%{BOT_STATS['total_profit_pct']:.2f}</span></div>
            <div class="stat">الصفقات المفتوحة حالياً: <b>{BOT_STATS['active_positions_count']}</b></div>
        </div>
        <p>🟢 البوت يعمل بقيمة 10$ لكل صفقة لتجاوز الحد الأدنى للمنصة.</p>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 [خادم الويب واللوحة] يعمل على المنفذ {port}", flush=True)

# --- 🔄 ميزة الاستدعاء الذاتي (Self-Ping Loop) ---
async def self_ping_loop():
    print("🔄 [المنشط الذاتي] بدأ العمل لمنع دخول السيرفر في وضع السكون...", flush=True)
    await asyncio.sleep(10)
    
    port = os.environ.get("PORT", 10000)
    render_url = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{port}")
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(render_url) as resp:
                    if resp.status == 200:
                        print(f"⚡ [استدعاء ذاتي ناجح] تم تنشيط خادم Render تلقائياً ({render_url})", flush=True)
            except Exception as e:
                print(f"⚠️ [تنبيه التنشيط الذاتي]: {e}", flush=True)
            
            await asyncio.sleep(180)

# --- إعدادات منصة OKX ---
API_KEY = '2bf148cb-c010-49fb-bffc-79f72720dfae'
SECRET_KEY = '1FBA3210BF6500BB1A1570EE9EC41A5C'
PASSPHRASE = 'Aa11223344##'

TRADE_AMOUNT_USDT = 10.0  # 🎯 الحد الأدنى: 10 دولار لكل صفقة

TAKE_PROFIT_PCT = 0.006  # 0.6% هدف ربح
STOP_LOSS_PCT = 0.004    # 0.4% وقف خسارة

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# --- المحرك الموحد لاقتناص الارتدادات والسكالبينغ ---
async def unified_scalping_bot():
    print("🚀 [البوت الذكي] بدأ العمل: اقتناص الانخفاضات بحجم 10 USDT...", flush=True)
    
    active_positions = {}
    target_watchlist = ['SOL/USDT', 'DOGE/USDT', 'BTC/USDT', 'ETH/USDT', 'XRP/USDT', 'ADA/USDT']

    while True:
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
            await exchange.load_markets()

            print(f"\n🔍 --- [تقرير الفحص الدوري] ---", flush=True)

            for symbol in target_watchlist:
                try:
                    ohlcv_5m = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=30)
                    if len(ohlcv_5m) < 25:
                        continue

                    df = pd.DataFrame(ohlcv_5m, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                    
                    df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
                    df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
                    df['rsi'] = calculate_rsi(df['close'], period=14)

                    prev_close = df['close'].iloc[-1]
                    ema_fast = df['ema_fast'].iloc[-1]
                    ema_slow = df['ema_slow'].iloc[-1]
                    rsi_val = df['rsi'].iloc[-1]

                    # الشروط
                    is_ema_cross = ema_fast > ema_slow and (40 <= rsi_val <= 68)
                    is_oversold_bounce = rsi_val <= 38

                    signal_type = None
                    if is_oversold_bounce:
                        signal_type = "شراء عند الانخفاض (RSI منخفض جداً) 🎯"
                    elif is_ema_cross:
                        signal_type = "تقاطع EMA صاعد 🚀"

                    if signal_type:
                        print(f"📊 {symbol}: السعر={prev_close} | RSI={rsi_val:.1f} | 🟢 [فرصة مناسبة: {signal_type}]", flush=True)
                    else:
                        print(f"📊 {symbol}: السعر={prev_close} | RSI={rsi_val:.1f} | ⚪ [لا توجد فرصة دخول حالياً]", flush=True)

                    # تنفيذ أمر الشراء بقيمة 10 USDT
                    if symbol not in active_positions or not active_positions[symbol]["in_pos"]:
                        if is_oversold_bounce or is_ema_cross:
                            # حساب كمية العملة التي تعادل 10 USDT بالضبط
                            crypto_amount = TRADE_AMOUNT_USDT / prev_close
                            
                            print(f"🚀 [تنفيذ أمر شراء بقيمة 10$] للرمز {symbol} (الكمية: {crypto_amount:.4f}) بسبب ({signal_type})...", flush=True)
                            order = await exchange.create_market_buy_order(symbol, crypto_amount)
                            fill_price = order.get('average') or prev_close
                            actual_amount = order.get('filled') or crypto_amount
                            
                            active_positions[symbol] = {
                                "in_pos": True,
                                "entry_price": fill_price,
                                "amount": actual_amount
                            }
                            BOT_STATS["active_positions_count"] = sum(1 for p in active_positions.values() if p["in_pos"])
                            print(f"✅ [تم فتح الصفقة بنجاح] للرمز {symbol} بسعر الشراء: {fill_price}", flush=True)

                except Exception as ex:
                    print(f"⚠️ خطأ أثناء فحص {symbol}: {ex}", flush=True)

            # متابعة الأرباح وإدارة الصفقات المفتوحة
            for symbol, pos in list(active_positions.items()):
                if pos["in_pos"]:
                    ticker = await exchange.fetch_ticker(symbol)
                    curr_price = ticker['last']
                    entry = pos["entry_price"]
                    amount = pos["amount"]
                    profit_ratio = (curr_price - entry) / entry

                    # هدف الربح 0.6%
                    if profit_ratio >= TAKE_PROFIT_PCT:
                        profit_usdt = (curr_price - entry) * amount
                        print(f"🎯 [تحقق هدف الربح TP] لـ {symbol} بنسبة +{profit_ratio*100:.2f}% | الربح: ${profit_usdt:.4f} USDT", flush=True)
                        await exchange.create_market_sell_order(symbol, amount)
                        active_positions[symbol]["in_pos"] = False
                        
                        BOT_STATS["total_trades"] += 1
                        BOT_STATS["winning_trades"] += 1
                        BOT_STATS["total_profit_usdt"] += profit_usdt
                        BOT_STATS["total_profit_pct"] += (profit_ratio * 100)
                        BOT_STATS["active_positions_count"] = sum(1 for p in active_positions.values() if p["in_pos"])

                    # وقف الخسارة 0.4%
                    elif profit_ratio <= -STOP_LOSS_PCT:
                        loss_usdt = (curr_price - entry) * amount
                        print(f"🛑 [وقف الخسارة SL] لـ {symbol} بنسبة {profit_ratio*100:.2f}% | الخسارة: ${loss_usdt:.4f} USDT", flush=True)
                        await exchange.create_market_sell_order(symbol, amount)
                        active_positions[symbol]["in_pos"] = False
                        
                        BOT_STATS["total_trades"] += 1
                        BOT_STATS["losing_trades"] += 1
                        BOT_STATS["total_profit_usdt"] += loss_usdt
                        BOT_STATS["total_profit_pct"] += (profit_ratio * 100)
                        BOT_STATS["active_positions_count"] = sum(1 for p in active_positions.values() if p["in_pos"])

        except Exception as e:
            print(f"❌ خطأ رئيسي في الدورة: {e}", flush=True)
        finally:
            if exchange:
                try:
                    await exchange.close()
                except Exception:
                    pass

        await asyncio.sleep(10)

# --- الدالة الرئيسية ---
async def main():
    await asyncio.gather(
        start_web_server(),
        self_ping_loop(),
        unified_scalping_bot()
    )

if __name__ == '__main__':
    asyncio.run(main())

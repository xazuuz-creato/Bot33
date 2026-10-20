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

# --- خادم ويب متطور يعرض لوحة الأرباح والنسبة المئوية ---
async def handle(request):
    html_content = f"""
    <!DOCTYPE html>
    <html dir="rtl" lang="ar">
    <head>
        <meta charset="UTF-8">
        <title>لوحة تحكم بوت السكالبينغ الموحد</title>
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
        <h1>🚀 حالة بوت السكالبينغ السريع</h1>
        <div class="card">
            <div class="stat">إجمالي الصفقات المنفذة: <b>{BOT_STATS['total_trades']}</b></div>
            <div class="stat">الصفقات الناجحة: <span class="profit">{BOT_STATS['winning_trades']}</span> | الخاسرة: <span class="loss">{BOT_STATS['losing_trades']}</span></div>
            <div class="stat">إجمالي الأرباح (USDT): <span class="{'profit' if BOT_STATS['total_profit_usdt'] >= 0 else 'loss'}">${BOT_STATS['total_profit_usdt']:.4f}</span></div>
            <div class="stat">نسبة الأرباح الإجمالية: <span class="{'profit' if BOT_STATS['total_profit_pct'] >= 0 else 'loss'}">%{BOT_STATS['total_profit_pct']:.2f}</span></div>
            <div class="stat">الصفقات المفتوحة حالياً: <b>{BOT_STATS['active_positions_count']}</b></div>
        </div>
        <p>🟢 البوت يعمل بنشاط لتنفيذ صفقات السكالبينغ السريعة.</p>
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

# --- 🔄 ميزة الاستدعاء الذاتي (Self-Ping Loop) لإبقاء Render مستيقظاً ---
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
TRADE_AMOUNT = 0.001

# إعدادات السكالبينغ والأهداف السريعة
TAKE_PROFIT_PCT = 0.006  # 0.6% هدف ربح سريع
STOP_LOSS_PCT = 0.004    # 0.4% وقف خسارة

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# --- المحرك الموحد المحدث للسرعة والدخول الفوري ---
async def unified_scalping_bot():
    print("🚀 [البوت السريع] بدأ العمل: مسح السوق واقتناص صفقات السكالبينغ...", flush=True)
    
    active_positions = {}
    target_watchlist = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'DOGE/USDT', 'ADA/USDT']

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

            print(f"🔍 [فحص دوري] جاري فحص المؤشرات لـ {len(target_watchlist)} عملة...", flush=True)

            for symbol in target_watchlist:
                try:
                    ohlcv_5m = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=30)
                    if len(ohlcv_5m) < 25:
                        continue

                    df = pd.DataFrame(ohlcv_5m, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                    
                    df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
                    df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
                    df['rsi'] = calculate_rsi(df['close'], period=14)
                    df['vol_ma'] = df['volume'].rolling(window=10).mean()

                    prev_close = df['close'].iloc[-2]
                    ema_fast = df['ema_fast'].iloc[-2]
                    ema_slow = df['ema_slow'].iloc[-2]
                    rsi_val = df['rsi'].iloc[-2]
                    curr_vol = df['volume'].iloc[-2]
                    avg_vol = df['vol_ma'].iloc[-2]

                    # --- شروط مرنة وسريعة للسكالبينغ ---
                    is_ema_cross = ema_fast > ema_slow
                    is_rsi_good = 45 < rsi_val < 75        # توسيع نطاق RSI لتقليل وقت الانتظار
                    is_volume_spike = curr_vol > (avg_vol * 1.05) # خفض شرط السيولة لدخول الصفقات أسرع

                    if symbol not in active_positions or not active_positions[symbol]["in_pos"]:
                        if is_ema_cross and is_rsi_good and is_volume_spike:
                            print(f"🚀 [إرسال أمر شراء سريع] للعملة {symbol} بالسعر {prev_close}...", flush=True)
                            order = await exchange.create_market_buy_order(symbol, TRADE_AMOUNT)
                            fill_price = order.get('average') or prev_close
                            
                            active_positions[symbol] = {
                                "in_pos": True,
                                "entry_price": fill_price
                            }
                            BOT_STATS["active_positions_count"] = sum(1 for p in active_positions.values() if p["in_pos"])
                            print(f"✅ [تم فتح صفقة سكالبينغ بنجاح] للرمز {symbol} بسعر: {fill_price}", flush=True)

                except Exception as ex:
                    print(f"⚠️ خطأ في معالجة العملة {symbol}: {ex}", flush=True)

            # متابعة الأرباح وإدارة الصفقات المفتوحة
            for symbol, pos in list(active_positions.items()):
                if pos["in_pos"]:
                    ticker = await exchange.fetch_ticker(symbol)
                    curr_price = ticker['last']
                    entry = pos["entry_price"]
                    profit_ratio = (curr_price - entry) / entry

                    if profit_ratio >= TAKE_PROFIT_PCT:
                        profit_usdt = (curr_price - entry) * TRADE_AMOUNT
                        print(f"🎯 [تحقق هدف الربح TP] لـ {symbol} بنسبة +{profit_ratio*100:.2f}% | الربح: ${profit_usdt:.4f} USDT", flush=True)
                        await exchange.create_market_sell_order(symbol, TRADE_AMOUNT)
                        active_positions[symbol]["in_pos"] = False
                        
                        BOT_STATS["total_trades"] += 1
                        BOT_STATS["winning_trades"] += 1
                        BOT_STATS["total_profit_usdt"] += profit_usdt
                        BOT_STATS["total_profit_pct"] += (profit_ratio * 100)
                        BOT_STATS["active_positions_count"] = sum(1 for p in active_positions.values() if p["in_pos"])

                    elif profit_ratio <= -STOP_LOSS_PCT:
                        loss_usdt = (curr_price - entry) * TRADE_AMOUNT
                        print(f"🛑 [وقف الخسارة SL] لـ {symbol} بنسبة {profit_ratio*100:.2f}% | الخسارة: ${loss_usdt:.4f} USDT", flush=True)
                        await exchange.create_market_sell_order(symbol, TRADE_AMOUNT)
                        active_positions[symbol]["in_pos"] = False
                        
                        BOT_STATS["total_trades"] += 1
                        BOT_STATS["losing_trades"] += 1
                        BOT_STATS["total_profit_usdt"] += loss_usdt
                        BOT_STATS["total_profit_pct"] += (profit_ratio * 100)
                        BOT_STATS["active_positions_count"] = sum(1 for p in active_positions.values() if p["in_pos"])

        except Exception as e:
            print(f"❌ خطأ رئيسي في دورة الفحص: {e}", flush=True)
        finally:
            if exchange:
                try:
                    await exchange.close()
                except Exception:
                    pass

        await asyncio.sleep(10) # تقليص وقت الانتظار بين الدورات لتسريع الاستجابة

# --- الدالة الرئيسية لتشغيل المهام معاً ---
async def main():
    await asyncio.gather(
        start_web_server(),
        self_ping_loop(),
        unified_scalping_bot()
    )

if __name__ == '__main__':
    asyncio.run(main())

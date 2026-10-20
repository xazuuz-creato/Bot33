import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import os
from aiohttp import web

# --- خادم ويب وهمي لإرضاء منصة Render ومنع السكون ---
async def handle(request):
    return web.Response(text="Unified Scalping Bot is running 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 [خادم الويب الوهمي] يعمل على المنفذ {port}", flush=True)

# --- إعدادات منصة OKX ---
API_KEY = '2bf148cb-c010-49fb-bffc-79f72720dfae'
SECRET_KEY = '1FBA3210BF6500BB1A1570EE9EC41A5C'
PASSPHRASE = 'Aa11223344##'
TRADE_AMOUNT = 0.001  # حجم الدخول التجريبي

# إعدادات السكالبينغ وإدارة المخاطر
TAKE_PROFIT_PCT = 0.006  # 0.6% جني أرباح
STOP_LOSS_PCT = 0.004    # 0.4% وقف خسارة

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# --- المحرك الموحد الشامل (بوت واحد لكل المهام) ---
async def unified_scalping_bot():
    print("🚀 [البوت الموحد] بدأ العمل: مراقبة السوق، فحص المؤشرات، وتنفيذ الصفقات...", flush=True)
    
    active_positions = {}

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

            # 1. قائمة العملات المستهدفة (محاكاة رصد الأخبار وحسابات الحيتان لأهم العملات)
            target_watchlist = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'DOGE/USDT', 'ADA/USDT']

            for symbol in target_watchlist:
                try:
                    # جلب بيانات الشموع لإطار 5 دقائق
                    ohlcv_5m = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=50)
                    if len(ohlcv_5m) < 40:
                        continue

                    df = pd.DataFrame(ohlcv_5m, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                    
                    # حساب المؤشرات
                    df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
                    df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
                    df['rsi'] = calculate_rsi(df['close'], period=14)
                    df['vol_ma'] = df['volume'].rolling(window=10).mean()

                    # استخدام الشمعة المغلقة السابقة لتفادي الإشارات الوهمية
                    prev_close = df['close'].iloc[-2]
                    ema_fast = df['ema_fast'].iloc[-2]
                    ema_slow = df['ema_slow'].iloc[-2]
                    rsi_val = df['rsi'].iloc[-2]
                    curr_vol = df['volume'].iloc[-2]
                    avg_vol = df['vol_ma'].iloc[-2]

                    # شروط الدخول للسكالبينغ (تقاطع EMA + RSI بين 50 و 70 + سيولة مفاجئة)
                    is_ema_cross = ema_fast > ema_slow
                    is_rsi_good = 50 < rsi_val < 70
                    is_volume_spike = curr_vol > (avg_vol * 1.3)

                    # إذا لم نكن في صفقة لهذه العملة وتحققت الشروط
                    if symbol not in active_positions or not active_positions[symbol]["in_pos"]:
                        if is_ema_cross and is_rsi_good and is_volume_spike:
                            print(f"🚀 [إرسال أمر شراء] للعملة {symbol} بالسعر {prev_close}...", flush=True)
                            order = await exchange.create_market_buy_order(symbol, TRADE_AMOUNT)
                            fill_price = order.get('average') or prev_close
                            
                            active_positions[symbol] = {
                                "in_pos": True,
                                "entry_price": fill_price
                            }
                            print(f"✅ [تم فتح الصفقة بنجاح] للرمز {symbol} بسعر: {fill_price}", flush=True)

                except Exception as ex:
                    print(f"⚠️ خطأ في معالجة العملة {symbol}: {ex}", flush=True)

            # 2. متابعة الصفقات المفتوحة للبيع عند تحقيق الهدف أو وقف الخسارة
            for symbol, pos in list(active_positions.items()):
                if pos["in_pos"]:
                    ticker = await exchange.fetch_ticker(symbol)
                    curr_price = ticker['last']
                    entry = pos["entry_price"]
                    profit_ratio = (curr_price - entry) / entry

                    if profit_ratio >= TAKE_PROFIT_PCT:
                        print(f"🎯 [هدف الربح TP] لـ {symbol} بنسبة +{profit_ratio*100:.2f}%.. جاري البيع.", flush=True)
                        await exchange.create_market_sell_order(symbol, TRADE_AMOUNT)
                        active_positions[symbol]["in_pos"] = False

                    elif profit_ratio <= -STOP_LOSS_PCT:
                        print(f"🛑 [وقف الخسارة SL] لـ {symbol} بنسبة -{profit_ratio*100:.2f}%.. بيع للحماية.", flush=True)
                        await exchange.create_market_sell_order(symbol, TRADE_AMOUNT)
                        active_positions[symbol]["in_pos"] = False

        except Exception as e:
            print(f"❌ خطأ رئيسي في دورة البوت: {e}", flush=True)
        finally:
            if exchange:
                try:
                    await exchange.close()
                except Exception:
                    pass

        await asyncio.sleep(15)

# --- الدالة الرئيسية لتشغيل خادم الويب والبوت معاً ---
async def main():
    await asyncio.gather(
        start_web_server(),
        unified_scalping_bot()
    )

if __name__ == '__main__':
    asyncio.run(main())

import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import os
from aiohttp import web

# --- خادم ويب وهمي لإرضاء منصة Render ومنع السكون ---
async def handle(request):
    return web.Response(text="Scalping Bot is running 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 [خادم الويب الوهمي] يعمل على المنفذ {port}", flush=True)

# --- 1. الإعدادات والبيانات الأساسية ---
API_KEY = '2bf148cb-c010-49fb-bffc-79f72720dfae'
SECRET_KEY = '1FBA3210BF6500BB1A1570EE9EC41A5C'
PASSPHRASE = 'Aa11223344##'

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
TRADE_AMOUNT = 0.001  # حجم الشراء التجريبي

# إعدادات السكالبينغ (النسب المئوية)
TAKE_PROFIT_PCT = 0.006  # جني الأرباح عند +0.6%
STOP_LOSS_PCT = 0.004    # وقف الخسارة عند -0.4%

# --- 2. دوال حساب المؤشرات السريعة ---
def calculate_ema(closes, period):
    series = pd.Series(closes)
    return series.ewm(span=period, adjust=False).mean()

# --- 3. المحرك الرئيسي للسكالبينغ (إطار 5 دقائق) ---
async def run_scalping_bot():
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

        print("⚡ [تشغيل محرك السكالبينغ السريع (5m) على OKX Demo] جاري مراقبة الزخم...", flush=True)

        # تتبع حالة الصفقات وسعر الدخول لكل عملة
        active_trades = {sym: {"in_position": False, "entry_price": 0.0} for sym in SYMBOLS}

        while True:
            for symbol in SYMBOLS:
                try:
                    # جلب بيانات إطار 5 دقائق
                    ohlcv_5m = await exchange.fetch_ohlcv(symbol, timeframe='5m', limit=50)
                    if len(ohlcv_5m) < 30:
                        continue

                    df = pd.DataFrame(ohlcv_5m, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                    
                    # حساب مؤشرات السكالبينغ (EMA 9 و EMA 21)
                    df['ema_fast'] = calculate_ema(df['close'], period=9)
                    df['ema_slow'] = calculate_ema(df['close'], period=21)

                    current_close = df['close'].iloc[-1]
                    ema_fast = df['ema_fast'].iloc[-1]
                    ema_slow = df['ema_slow'].iloc[-1]

                    trade_info = active_trades[symbol]

                    # طباعة الحالة في السجلات
                    print(f"📊 [{symbol}] السعر: {current_close} | السريع: {ema_fast:.2f} | البطيء: {ema_slow:.2f}", flush=True)

                    # إذا لم نكن في صفقة، نبحث عن فرصة شراء (تقاطع إيجابي للصعود)
                    if not trade_info["in_position"]:
                        if ema_fast > ema_slow:
                            print(f"🚀 [إرسال أمر شراء سكالبينغ] للرمز {symbol}...", flush=True)
                            order = await exchange.create_market_buy_order(symbol, TRADE_AMOUNT)
                            
                            # اعتماد سعر التنفيذ الفعلي أو السعر الحالي
                            fill_price = order.get('average') or current_close
                            trade_info["in_position"] = True
                            trade_info["entry_price"] = fill_price
                            print(f"✅ [تم الشراء بنجاح بسعر]: {fill_price}", flush=True)

                    # إذا كنا في صفقة، نراقب أهداف جني الأرباح أو وقف الخسارة
                    else:
                        entry_price = trade_info["entry_price"]
                        profit_ratio = (current_close - entry_price) / entry_price

                        # تحقق من تحقق هدف الربح أو وقف الخسارة
                        if profit_ratio >= TAKE_PROFIT_PCT:
                            print(f"🎯 [حقق هدف الربح TP] للرمز {symbol} بنسبة {profit_ratio*100:.2f}%.. جاري البيع...", flush=True)
                            await exchange.create_market_sell_order(symbol, TRADE_AMOUNT)
                            trade_info["in_position"] = False
                            print(f"✅ [تم إغلاق الصفقة بربح بنجاح]", flush=True)

                        elif profit_ratio <= -STOP_LOSS_PCT:
                            print(f"🛑 [ضرب وقف الخسارة SL] للرمز {symbol} بنسبة {profit_ratio*100:.2f}%.. جاري البيع للحماية...", flush=True)
                            await exchange.create_market_sell_order(symbol, TRADE_AMOUNT)
                            trade_info["in_position"] = False
                            print(f"⚠️ [تم إغلاق الصفقة لوقف الخسارة]", flush=True)

                except Exception as e:
                    print(f"⚠️ خطأ أثناء تداول {symbol}: {e}", flush=True)

            await asyncio.sleep(10)  # فحص سريع كل 10 ثوانٍ للسكالبينغ

    except Exception as e:
        print(f"❌ خطأ رئيسي في محرك الاتصال بـ OKX: {e}", flush=True)
    finally:
        try:
            await exchange.close()
        except UnboundLocalError:
            pass

async def main():
    await start_web_server()
    await run_scalping_bot()

if __name__ == '__main__':
    asyncio.run(main())


'use strict';

const el = id => document.getElementById(id);
const fmt = n => Number(n || 0).toFixed(4);

let key = '';
let config = null;
let symbols = [];
let selected = [];
let positions = [];
let history = [];

let socket = null;
let generation = 0;
let connected = false;
let scope = 'top12';

let books = {};
let sentAt = {};
let trades = [];

let entryPending = new Set();
let exitPending = new Set();
let lastEntry = {};

let refreshing = false;
let runningTest = false;
let runningScan = false;

function log(message, bad = false) {
    const div = document.createElement('div');

    div.className = bad
        ? 'text-rose-400'
        : 'text-emerald-300';

    div.textContent =
        `[${new Date().toLocaleTimeString('ar-SA')}] ${message}`;

    el('consoleLog').prepend(div);

    while (el('consoleLog').children.length > 80) {
        el('consoleLog').lastElementChild.remove();
    }
}

function clearConsole() {
    el('consoleLog').textContent = '';
}

async function api(path, data, method = 'POST') {
    const controller = new AbortController();

    const timeout = setTimeout(
        () => controller.abort(),
        6500
    );

    try {
        const response = await fetch(path, {
            method,
            headers: {
                'Content-Type': 'application/json',
                'X-Access-Key': key
            },
            ...(data === undefined
                ? {}
                : { body: JSON.stringify(data) }),
            signal: controller.signal
        });

        const body = await response.json();

        if (!response.ok) {
            throw Error(
                typeof body.detail === 'string'
                    ? body.detail
                    : `HTTP ${response.status}`
            );
        }

        return body;

    } finally {
        clearTimeout(timeout);
    }
}

function setScope(next) {
    scope = next;

    selected = next === 'top12'
        ? symbols.slice(0, 12)
        : symbols.slice(0, 30);

    el('scopeTopBtn').className =
        'btn ' + (next === 'top12' ? 'selected' : '');

    el('scopeAllBtn').className =
        'btn ' + (next === 'all' ? 'selected' : '');

    reconnect();
}

function reconnect() {
    const gen = ++generation;
    connected = false;

    if (socket) {
        socket.onclose = null;
        socket.close();
        socket = null;
    }

    const watching = [
        ...new Set([
            ...selected,
            ...positions.map(p => p.symbol)
        ])
    ];

    if (!watching.length) return;

    const streams = watching.flatMap(s => [
        `${s.toLowerCase()}@depth5@100ms`,
        `${s.toLowerCase()}@aggTrade`
    ]);

    socket = new WebSocket(
        'wss://stream.binance.com:9443/stream?streams='
        + streams.join('/')
    );

    socket.onopen = () => {
        if (gen === generation) {
            connected = true;
            el('wsStatus').textContent = 'متصل';
            log('تم الاتصال ببث Binance');
        }
    };

    socket.onmessage = event => {
        if (gen !== generation) return;

        let p;

        try {
            p = JSON.parse(event.data);
        } catch {
            return;
        }

        const d = p.data;
        if (!d) return;

        const symbol = String(
            d.s || p.stream?.split('@')[0] || ''
        ).toUpperCase();

        if (
            d.bids &&
            d.asks &&
            d.bids.length &&
            d.asks.length
        ) {
            const bid = Number(d.bids[0][0]);
            const ask = Number(d.asks[0][0]);

            if (!(bid > 0 && ask > bid)) return;

            books[symbol] = {
                bid,
                ask,
                at: Date.now()
            };

            if (
                Date.now() - (sentAt[symbol] || 0) >= 350
            ) {
                sentAt[symbol] = Date.now();

                api('/api/market-data/book', {
                    symbol,
                    bids: d.bids,
                    asks: d.asks
                }).catch(e => {
                    log(
                        'دفتر الطلبات: ' + e.message,
                        true
                    );
                });
            }

        } else if (d.e === 'aggTrade') {
            const price = Number(d.p);
            const quantity = Number(d.q);

            if (price > 0 && quantity > 0) {
                trades.push({
                    symbol,
                    price,
                    quantity,
                    isBuyerMaker: Boolean(d.m)
                });
            }

            if (trades.length > 1000) {
                trades.splice(
                    0,
                    trades.length - 1000
                );
            }
        }
    };

    socket.onclose = () => {
        if (gen !== generation) return;

        connected = false;
        el('wsStatus').textContent = 'منقطع';

        log(
            'انقطع WebSocket؛ إعادة المحاولة بعد 3 ثوانٍ',
            true
        );

        setTimeout(() => {
            if (gen === generation) reconnect();
        }, 3000);
    };

    socket.onerror = () => {
        connected = false;
        el('wsStatus').textContent = 'خطأ اتصال';
    };
}

async function flushTrades() {
    if (!key || !trades.length) return;

    const batch = trades.splice(0, 200);

    try {
        await api('/api/market-data/trades', {
            trades: batch
        });
    } catch (e) {
        log(
            'تعذر إرسال الصفقات المنفذة: ' + e.message,
            true
        );
    }
}

async function refresh() {
    if (!key || refreshing) return;

    refreshing = true;

    try {
        const data = await api(
            '/api/state',
            undefined,
            'GET'
        );

        const before = positions
            .map(p => p.symbol)
            .join(',');

        positions = data.positions;
        history = data.history;

        el('balanceDisplay').textContent =
            '$' + Number(data.cashUsdt).toFixed(2);

        render();

        if (
            before !== positions
                .map(p => p.symbol)
                .join(',')
        ) {
            reconnect();
        }

    } catch (e) {
        log(
            'فشل مزامنة السيرفر: ' + e.message,
            true
        );
    } finally {
        refreshing = false;
    }
}

async function scan() {
    if (
        !connected ||
        !key ||
        runningScan ||
        !config
    ) {
        return;
    }

    runningScan = true;

    try {
        for (const symbol of selected) {
            if (
                positions.length + entryPending.size
                >= config.maxPositions
            ) {
                break;
            }

            if (
                positions.some(p => p.symbol === symbol)
                || entryPending.has(symbol)
            ) {
                continue;
            }

            if (
                !books[symbol] ||
                Date.now() - books[symbol].at > 2000
            ) {
                continue;
            }

            if (
                Date.now() - (lastEntry[symbol] || 0)
                < 1700
            ) {
                continue;
            }

            lastEntry[symbol] = Date.now();
            entryPending.add(symbol);

            try {
                const signal = await api(
                    '/api/evaluate-entry',
                    { symbol }
                );

                if (!signal.enter) continue;

                const budget = Number(
                    el('budgetInput').value
                );

                const hold = Number(
                    el('timeoutRange').value
                );

                if (
                    !Number.isFinite(budget) ||
                    budget < 10 ||
                    budget > 100000
                ) {
                    throw Error('ميزانية غير صالحة');
                }

                const order = await api(
                    '/api/execute-order',
                    {
                        symbol,
                        action: 'BUY',
                        amountUsdt: budget,
                        signalToken: signal.signalToken,
                        maxHoldSeconds: hold,
                        useDemo: true
                    }
                );

                log(
                    `شراء تجريبي مؤكد: ${symbol} | ` +
                    `عمولة ${fmt(order.feeUsdt)}`
                );

                await refresh();

            } catch (e) {
                log(
                    `رفض/فشل شراء ${symbol}: ${e.message}`,
                    true
                );

            } finally {
                entryPending.delete(symbol);
            }
        }
    } finally {
        runningScan = false;
    }
}

async function checkExits() {
    for (const p of [...positions]) {
        if (exitPending.has(p.symbol)) continue;

        exitPending.add(p.symbol);

        try {
            const decision = await api(
                '/api/position/evaluate-exit',
                { symbol: p.symbol }
            );

            if (decision.reason === 'STALE_DATA') {
                log(
                    `تحذير: بيانات ${p.symbol} قديمة، ` +
                    'المركز دون متابعة فعالة',
                    true
                );
                continue;
            }

            if (!decision.exit) continue;

            const order = await api(
                '/api/execute-order',
                {
                    symbol: p.symbol,
                    action: 'SELL',
                    reason: decision.reason,
                    useDemo: true
                }
            );

            log(
                `بيع تجريبي مؤكد ${p.symbol} | ` +
                `الصافي ${fmt(order.closedTrade.net)} USDT`
            );

            await refresh();

        } catch (e) {
            log(
                `فشل متابعة ${p.symbol}: ${e.message}`,
                true
            );

        } finally {
            exitPending.delete(p.symbol);
        }
    }
}

function render() {
    el('activeCountBadge').textContent =
        `${positions.length} مراكز`;

    const active = el('activeTradesTbody');
    active.textContent = '';

    for (const p of positions) {
        const bid = books[p.symbol]?.bid || p.entry;

        const estimate =
            p.qty *
            bid *
            (1 - (config?.slippageRatePerSide || 0)) *
            (1 - (config?.feeRatePerSide || 0))
            - p.cost
            - p.buy_fee;

        const tr = document.createElement('tr');

        const values = [
            p.symbol,
            fmt(p.entry),
            fmt(bid),
            fmt(p.cost + p.buy_fee),
            fmt(estimate) + ' USDT',
            Math.floor(
                Date.now() / 1000 - p.opened
            ) + 'ث',
            fmt(p.stop) + ' / ' + fmt(p.target)
        ];

        values.forEach(v => {
            const td = document.createElement('td');
            td.textContent = v;
            tr.appendChild(td);
        });

        active.appendChild(tr);
    }

    if (!positions.length) {
        active.innerHTML =
            '<tr><td colspan="7">' +
            'لا توجد صفقات مفتوحة</td></tr>';
    }

    const finished = el('historyTradesTbody');
    finished.textContent = '';

    const net = history.reduce(
        (a, t) => a + t.net,
        0
    );

    const wins = history.filter(
        t => t.net > 0
    ).length;

    const rate = history.length
        ? (wins / history.length * 100).toFixed(1)
        : 0;

    el('historyStatsBadge').textContent =
        `صافي آخر ${history.length} صفقة: ` +
        `${fmt(net)} USDT | نجاح ${rate}%`;

    for (const t of history) {
        const tr = document.createElement('tr');

        const values = [
            t.symbol,
            fmt(t.entry),
            fmt(t.exit),
            fmt(t.buy_fee + t.sell_fee),
            fmt(t.net),
            Math.round(t.closed - t.opened) + 'ث',
            t.reason
        ];

        values.forEach(v => {
            const td = document.createElement('td');
            td.textContent = v;
            tr.appendChild(td);
        });

        finished.appendChild(tr);
    }

    if (!history.length) {
        finished.innerHTML =
            '<tr><td colspan="7">' +
            'لا توجد صفقات مكتملة</td></tr>';
    }
}

async function runSelfTest() {
    if (runningTest) return;

    key = el('accessKey').value.trim();

    if (!key) {
        alert('أدخل مفتاح APP_ACCESS_KEY أولاً');
        return;
    }

    runningTest = true;
    el('testButton').disabled = true;
    el('testResults').textContent = 'جارٍ الفحص...';

    try {
        const report = await api(
            '/api/self-test',
            {}
        );

        const box = el('testResults');
        box.textContent = '';

        for (const r of report.results) {
            const line = document.createElement('div');

            line.className =
                r.status === 'PASS'
                    ? 'text-emerald-400'
                    : r.status === 'FAIL'
                    ? 'text-rose-400'
                    : 'text-amber-400';

            line.textContent =
                `${r.status} | ${r.name} | ${r.detail}`;

            box.appendChild(line);
        }

        log(
            `الفحص: ${report.passed} ناجح، ` +
            `${report.failed} فاشل، ` +
            `${report.unavailable} غير متاح`
        );

    } catch (e) {
        el('testResults').textContent =
            'تعذر إجراء الفحص: ' + e.message;

    } finally {
        runningTest = false;
        el('testButton').disabled = false;
    }
}

async function startApp() {
    key = el('accessKey').value.trim();

    if (!key) {
        alert('أدخل مفتاح APP_ACCESS_KEY');
        return;
    }

    try {
        config = await api(
            '/api/config',
            undefined,
            'GET'
        );

        const result = await api(
            '/api/symbols',
            undefined,
            'GET'
        );

        if (!result.verified) {
            throw Error(
                'لم يتم التحقق من أزواج Spot'
            );
        }

        symbols = config.symbols.filter(
            s => result.symbols.includes(s)
        );

        await refresh();
        setScope(scope);

        log(
            `بدأت المراقبة التجريبية لعدد ` +
            `${selected.length} زوج`
        );

    } catch (e) {
        log(
            'تعذر بدء المراقبة: ' + e.message,
            true
        );
    }
}

function updateTimeoutValue(value) {
    el('timeoutDisplay').textContent =
        value + ' ثانية';
}

window.addEventListener('load', () => {
    el('liveBtn').disabled = true;

    setInterval(flushTrades, 400);
    setInterval(scan, 1700);
    setInterval(checkExits, 1000);
    setInterval(refresh, 5000);
    setInterval(render, 1000);
});

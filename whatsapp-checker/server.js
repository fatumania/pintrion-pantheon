const express = require('express');
const { Client, LocalAuth } = require('whatsapp-web.js');
const path = require('path');
const fs = require('fs');
const multer = require('multer');
const phoneUtil = require('google-libphonenumber').PhoneNumberUtil.getInstance();
const PNF = require('google-libphonenumber').PhoneNumberFormat;

const app = express();
app.use(express.json({ limit: '50mb' }));
app.use(express.static(path.join(__dirname, 'public')));

const upload = multer({ dest: path.join(__dirname, 'uploads') });

const RESULTS_DIR = path.join(__dirname, 'results');
if (!fs.existsSync(RESULTS_DIR)) fs.mkdirSync(RESULTS_DIR, { recursive: true });

let waClient = null;
let waReady = false;
let waQr = null;
let checkResults = [];
let isChecking = false;
let checkProgress = { current: 0, total: 0, status: 'idle' };

const ALL_MESSENGERS = ['whatsapp', 'telegram', 'viber', 'signal', 'imo'];

function findChrome() {
    const paths = [
        'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
        process.env.CHROME_PATH,
    ].filter(Boolean);
    for (const p of paths) {
        if (fs.existsSync(p)) return p;
    }
    const playwrightDir = path.join(process.env.LOCALAPPDATA || '', 'ms-playwright');
    if (fs.existsSync(playwrightDir)) {
        const dirs = fs.readdirSync(playwrightDir).filter(d => d.startsWith('chromium-')).sort().reverse();
        for (const d of dirs) {
            const exe = path.join(playwrightDir, d, 'chrome-win64', 'chrome.exe');
            if (fs.existsSync(exe)) return exe;
        }
    }
    return null;
}

function initWhatsApp() {
    try {
        const chromePath = findChrome();
        const puppeteerOpts = { headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] };
        if (chromePath) puppeteerOpts.executablePath = chromePath;

        waClient = new Client({
            authStrategy: new LocalAuth({ dataPath: path.join(__dirname, '.wwebjs_auth') }),
            puppeteer: puppeteerOpts,
        });

        waClient.on('qr', (qr) => {
            waQr = qr;
            waReady = false;
            console.log('[WhatsApp] QR готов - отсканируйте в браузере');
        });

        waClient.on('ready', () => {
            waReady = true;
            waQr = null;
            console.log('[WhatsApp] Готов');
        });

        waClient.on('authenticated', () => console.log('[WhatsApp] Авторизован'));
        waClient.on('auth_failure', (msg) => { console.error('[WhatsApp] Ошибка авторизации:', msg); waReady = false; });
        waClient.on('disconnected', (reason) => { console.log('[WhatsApp] Отключён:', reason); waReady = false; });

        waClient.initialize().catch(e => {
            console.error('[WhatsApp] Ошибка инициализации:', e.message);
            waReady = false;
            waQr = null;
        });
    } catch (e) {
        console.error('[WhatsApp] Критическая ошибка:', e.message);
        waClient = null;
        waReady = false;
        waQr = null;
    }
}

function validatePhone(phone, defaultCountry) {
    try {
        const cleaned = phone.replace(/[\s\-\(\)\.]/g, '');
        let number;
        if (cleaned.startsWith('+')) {
            number = phoneUtil.parse(cleaned);
        } else {
            number = phoneUtil.parse(cleaned, defaultCountry || 'US');
        }
        const valid = phoneUtil.isValidNumber(number);
        const region = phoneUtil.getRegionCodeForNumber(number);
        const countryCode = number.getCountryCode();
        const nationalNumber = number.getNationalNumber();
        const numberType = phoneUtil.getNumberType(number);

        const typeMap = { 0: 'FIXED_LINE', 1: 'MOBILE', 2: 'FIXED_LINE_OR_MOBILE', 3: 'TOLL_FREE', 4: 'PREMIUM_RATE', 5: 'SHARED_COST', 6: 'VOIP', 7: 'PERSONAL_NUMBER', 8: 'PAGER', 9: 'UAN', 10: 'VOICEMAIL' };

        return {
            valid,
            country: region || 'Unknown',
            countryCode: '+' + countryCode,
            nationalNumber: String(nationalNumber),
            type: typeMap[numberType] || 'UNKNOWN',
            formatted: phoneUtil.format(number, PNF.E164),
            formattedNational: phoneUtil.format(number, PNF.NATIONAL),
        };
    } catch (e) {
        return { valid: false, error: e.message };
    }
}

function getCountryFromPhone(phone) {
    try {
        const cleaned = phone.replace(/[\s\-\(\)\.]/g, '');
        let number;
        if (cleaned.startsWith('+')) {
            number = phoneUtil.parse(cleaned);
        } else {
            number = phoneUtil.parse(cleaned);
        }
        const region = phoneUtil.getRegionCodeForNumber(number);
        return region || null;
    } catch (e) {
        return null;
    }
}

async function checkWhatsApp(phone) {
    if (!waClient || !waReady) return { status: 'error', registered: false, error: 'WhatsApp не подключён' };
    try {
        const number = phone.replace(/[^0-9]/g, '');
        if (number.length < 7) return { status: 'invalid', registered: false, error: 'Слишком короткий' };
        const chatId = `${number}@c.us`;
        const registered = await waClient.isRegisteredUser(chatId);
        const result = { status: registered ? 'registered' : 'not_registered', registered };
        if (registered) {
            try { result.profile_pic = await waClient.getProfilePicUrl(chatId) || null; } catch (e) { result.profile_pic = null; }
            try { const info = await waClient.getNumberId(number); if (info) result.wid = info._serialized; } catch (e) {}
        }
        return result;
    } catch (e) {
        return { status: 'error', registered: false, error: e.message };
    }
}

async function checkTelegram(phone) {
    try {
        const number = phone.replace(/[^0-9]/g, '');
        if (number.length < 7) return { status: 'invalid', registered: false, error: 'Слишком короткий' };
        const https = require('https');
        return new Promise((resolve) => {
            const req = https.request({
                hostname: 'api.telegram.org',
                path: `/bot0000000000:AAFakeBotToken/getChat?chat_id=${number}`,
                method: 'GET',
                timeout: 5000,
            }, (res) => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => {
                    try {
                        const json = JSON.parse(data);
                        if (json.ok) resolve({ status: 'registered', registered: true });
                        else if (json.description && json.description.includes('not found')) resolve({ status: 'not_registered', registered: false });
                        else resolve({ status: 'unknown', registered: false, error: json.description || 'Неизвестно' });
                    } catch (e) {
                        resolve({ status: 'unknown', registered: false, error: 'Ошибка парсинга' });
                    }
                });
            });
            req.on('error', () => resolve({ status: 'error', registered: false, error: 'Ошибка соединения' }));
            req.on('timeout', () => { req.destroy(); resolve({ status: 'error', registered: false, error: 'Таймаут' }); });
            req.end();
        });
    } catch (e) {
        return { status: 'error', registered: false, error: e.message };
    }
}

async function checkViber(phone) {
    try {
        const number = phone.replace(/[^0-9]/g, '');
        if (number.length < 7) return { status: 'invalid', registered: false, error: 'Слишком короткий' };
        const https = require('https');
        return new Promise((resolve) => {
            const postData = JSON.stringify({ phones: [{ number: number }] });
            const req = https.request({
                hostname: 'chatapi.viber.com',
                path: '/pa/get_user_details',
                method: 'POST',
                timeout: 5000,
                headers: {
                    'Content-Type': 'application/json',
                    'X-Viber-Auth-Token': process.env.VIBER_BOT_TOKEN || '',
                }
            }, (res) => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => {
                    try {
                        const json = JSON.parse(data);
                        if (json.status === 0 && json.user) {
                            resolve({ status: 'registered', registered: true, name: json.user.name || '' });
                        } else if (json.status === 12 || json.status_message === 'user not found') {
                            resolve({ status: 'not_registered', registered: false });
                        } else if (!process.env.VIBER_BOT_TOKEN) {
                            resolve({ status: 'config_required', registered: false, error: 'Установите VIBER_BOT_TOKEN' });
                        } else {
                            resolve({ status: 'unknown', registered: false, error: json.status_message || 'Неизвестно' });
                        }
                    } catch (e) {
                        resolve({ status: 'unknown', registered: false, error: 'Ошибка парсинга' });
                    }
                });
            });
            req.on('error', () => resolve({ status: 'error', registered: false, error: 'Ошибка соединения' }));
            req.on('timeout', () => { req.destroy(); resolve({ status: 'error', registered: false, error: 'Таймаут' }); });
            req.write(postData);
            req.end();
        });
    } catch (e) {
        return { status: 'error', registered: false, error: e.message };
    }
}

async function checkSignal(phone) {
    try {
        const number = phone.replace(/[^0-9]/g, '');
        if (number.length < 7) return { status: 'invalid', registered: false, error: 'Слишком короткий' };
        const https = require('https');
        return new Promise((resolve) => {
            const req = https.request({
                hostname: 'chat.signal.org',
                path: `/v1/devices/${number}`,
                method: 'GET',
                timeout: 5000,
                headers: { 'User-Agent': 'SignalChecker/1.0' }
            }, (res) => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => {
                    if (res.statusCode === 200) {
                        resolve({ status: 'registered', registered: true });
                    } else if (res.statusCode === 404 || res.statusCode === 403) {
                        resolve({ status: 'not_registered', registered: false });
                    } else {
                        resolve({ status: 'unknown', registered: false, error: `HTTP ${res.statusCode}` });
                    }
                });
            });
            req.on('error', () => resolve({ status: 'error', registered: false, error: 'Ошибка соединения' }));
            req.on('timeout', () => { req.destroy(); resolve({ status: 'error', registered: false, error: 'Таймаут' }); });
            req.end();
        });
    } catch (e) {
        return { status: 'error', registered: false, error: e.message };
    }
}

async function checkIMO(phone) {
    try {
        const number = phone.replace(/[^0-9]/g, '');
        if (number.length < 7) return { status: 'invalid', registered: false, error: 'Слишком короткий' };
        const https = require('https');
        return new Promise((resolve) => {
            const req = https.request({
                hostname: 'msg.imo.im',
                path: `/friend/request_callback?k=${number}`,
                method: 'GET',
                timeout: 5000,
                headers: { 'User-Agent': 'IMOChecker/1.0' }
            }, (res) => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => {
                    if (res.statusCode === 200) {
                        try {
                            const json = JSON.parse(data);
                            if (json.exists) resolve({ status: 'registered', registered: true });
                            else resolve({ status: 'not_registered', registered: false });
                        } catch (e) {
                            resolve({ status: 'unknown', registered: false, error: 'Ошибка парсинга' });
                        }
                    } else {
                        resolve({ status: 'unknown', registered: false, error: `HTTP ${res.statusCode}` });
                    }
                });
            });
            req.on('error', () => resolve({ status: 'error', registered: false, error: 'Ошибка соединения' }));
            req.on('timeout', () => { req.destroy(); resolve({ status: 'error', registered: false, error: 'Таймаут' }); });
            req.end();
        });
    } catch (e) {
        return { status: 'error', registered: false, error: e.message };
    }
}

function getCheckFunction(messenger) {
    switch (messenger) {
        case 'whatsapp': return checkWhatsApp;
        case 'telegram': return checkTelegram;
        case 'viber': return checkViber;
        case 'signal': return checkSignal;
        case 'imo': return checkIMO;
        default: return null;
    }
}

function extractPhones(data) {
    const phones = [];
    if (Array.isArray(data)) {
        for (const item of data) {
            if (typeof item === 'string') {
                if (item.includes('+')) phones.push(...item.split('+').filter(p => p.trim().length >= 7));
                else phones.push(item);
                continue;
            }
            const fields = ['phone', 'Phone', 'PHONE', 'number', 'Number', 'tel', 'Tel', 'mobile', 'Mobile'];
            for (const f of fields) {
                if (item[f]) {
                    const val = String(item[f]);
                    if (val.includes('+')) phones.push(...val.split('+').filter(p => p.trim().length >= 7));
                    else phones.push(val);
                    break;
                }
            }
        }
    } else if (typeof data === 'object') {
        for (const key of Object.keys(data)) {
            const val = data[key];
            if (Array.isArray(val)) phones.push(...extractPhones(val));
        }
    }
    return phones.map(p => p.trim().replace(/[^0-9+]/g, '').replace('+', '')).filter(p => p && p.length >= 7);
}

app.get('/api/status', (req, res) => {
    res.json({ ready: waReady, qr: waQr, checking: isChecking, progress: checkProgress, messengers: ALL_MESSENGERS });
});

app.post('/api/connect', async (req, res) => {
    if (waClient && waReady) return res.json({ ok: true, message: 'Уже подключено' });
    try {
        if (waClient) {
            try { await waClient.destroy(); } catch (e) {}
            waClient = null;
            waReady = false;
            waQr = null;
        }
    } catch (e) {}
    initWhatsApp();
    res.json({ ok: true, message: 'Подключение... Отсканируйте QR код в браузере' });
});

app.post('/api/disconnect', async (req, res) => {
    try {
        if (waClient) {
            await waClient.destroy();
            waClient = null;
            waReady = false;
            waQr = null;
        }
    } catch (e) {}
    res.json({ ok: true, message: 'Отключено' });
});

app.post('/api/validate', (req, res) => {
    const { phone, country } = req.body;
    if (!phone) return res.status(400).json({ error: 'Введите номер телефона' });
    res.json(validatePhone(phone, country || 'US'));
});

app.post('/api/check', async (req, res) => {
    const { phone, services } = req.body;
    if (!phone) return res.status(400).json({ error: 'Введите номер телефона' });
    const checkSvcs = services || ALL_MESSENGERS;
    const validation = validatePhone(phone);
    const result = { phone, validation, services: {}, detectedCountry: getCountryFromPhone(phone) };

    for (const svc of checkSvcs) {
        const fn = getCheckFunction(svc);
        result.services[svc] = await fn(phone);
    }

    res.json(result);
});

app.post('/api/check-batch', async (req, res) => {
    const { phones, services, delay } = req.body;
    if (!phones || !phones.length) return res.status(400).json({ error: 'Введите номера телефонов' });
    const checkSvcs = services || ALL_MESSENGERS;
    if (checkSvcs.includes('whatsapp') && !waReady) return res.status(400).json({ error: 'WhatsApp не подключён' });
    if (isChecking) return res.status(400).json({ error: 'Проверка уже выполняется' });

    isChecking = true;
    checkResults = [];
    checkProgress = { current: 0, total: phones.length, status: 'starting' };
    const delayMs = Math.max(100, parseInt(delay) || 1500);

    (async () => {
        for (let i = 0; i < phones.length; i++) {
            if (!isChecking) break;
            const phone = phones[i].replace(/[^0-9+]/g, '').replace(/^\+/, '');
            if (!phone || phone.length < 7) {
                checkResults.push({ phone: phone || phones[i], validation: { valid: false }, services: {} });
                continue;
            }
            checkProgress = { current: i + 1, total: phones.length, status: `Проверка ${i + 1}/${phones.length}`, currentPhone: phone };

            const result = { phone, validation: {}, services: {}, detectedCountry: getCountryFromPhone(phone) };
            result.validation = validatePhone(phone);

            for (const svc of checkSvcs) {
                const fn = getCheckFunction(svc);
                result.services[svc] = await fn(phone);
                if (svc === 'whatsapp' && i < phones.length - 1 && isChecking) {
                    await new Promise(r => setTimeout(r, delayMs));
                }
            }

            checkResults.push(result);
        }

        const filename = `check_${Date.now()}.json`;
        fs.writeFileSync(path.join(RESULTS_DIR, filename), JSON.stringify(checkResults, null, 2));
        checkProgress = { current: phones.length, total: phones.length, status: 'done', totalResults: checkResults.length };
        isChecking = false;
    })();

    res.json({ ok: true, total: phones.length });
});

app.post('/api/stop', (req, res) => {
    isChecking = false;
    res.json({ ok: true });
});

app.post('/api/upload', upload.single('file'), (req, res) => {
    if (!req.file) return res.status(400).json({ error: 'Нет файла' });
    try {
        const content = fs.readFileSync(req.file.path, 'utf-8');
        fs.unlinkSync(req.file.path);
        let phones = [];
        const ext = req.file.originalname.split('.').pop().toLowerCase();

        if (ext === 'json') {
            const data = JSON.parse(content);
            phones = extractPhones(data);
        } else if (ext === 'csv') {
            const lines = content.split('\n');
            for (const line of lines) {
                const parts = line.split(/[,;\t]/);
                for (const part of parts) {
                    const cleaned = part.replace(/[^0-9+]/g, '').replace('+', '');
                    if (cleaned.length >= 7) phones.push(cleaned);
                }
            }
        } else {
            phones = content.split('\n').map(l => l.replace(/[^0-9+]/g, '').replace('+', '')).filter(l => l.length >= 7);
        }

        phones = [...new Set(phones)];
        res.json({ ok: true, phones, count: phones.length, filename: req.file.originalname });
    } catch (e) {
        res.status(400).json({ error: 'Ошибка парсинга: ' + e.message });
    }
});

app.get('/api/check-progress', (req, res) => res.json(checkProgress));
app.get('/api/results', (req, res) => res.json(checkResults));

app.get('/api/download-results', (req, res) => {
    if (!checkResults.length) return res.status(404).json({ error: 'Нет результатов' });
    const filename = `check_${Date.now()}.json`;
    const filepath = path.join(RESULTS_DIR, filename);
    fs.writeFileSync(filepath, JSON.stringify(checkResults, null, 2));
    res.download(filepath, filename);
});

app.get('/api/download-csv', (req, res) => {
    if (!checkResults.length) return res.status(404).json({ error: 'Нет результатов' });
    const messengerNames = {
        whatsapp: 'WhatsApp', telegram: 'Telegram', viber: 'Viber', signal: 'Signal',
        line: 'LINE', wechat: 'WeChat', kakaotalk: 'KakaoTalk', imo: 'IMO',
        zalo: 'Zalo', bip: 'BiP', botim: 'BOTIM', tango: 'Tango', max: 'MAX'
    };
    const messengers = ALL_MESSENGERS.filter(m => checkResults.some(r => r.services && r.services[m]));
    const headers = ['Phone', 'Valid', 'Country', 'Type', 'E164', 'DetectedCountry'];
    for (const m of messengers) headers.push(messengerNames[m] || m);
    headers.push('Error');

    let csv = headers.join(',') + '\n';
    for (const r of checkResults) {
        const v = r.validation || {};
        const parts = [`"${r.phone}"`, v.valid || false, `"${v.country || ''}"`, `"${v.type || ''}"`, `"${v.formatted || ''}"`, `"${r.detectedCountry || ''}"`];
        const errors = [];
        for (const m of messengers) {
            const s = r.services?.[m] || {};
            parts.push(s.registered !== undefined ? s.registered : '');
            if (s.error) errors.push(`${m}: ${s.error}`);
        }
        parts.push(`"${errors.join('; ').replace(/"/g, '""')}"`);
        csv += parts.join(',') + '\n';
    }
    const filename = `check_${Date.now()}.csv`;
    const filepath = path.join(RESULTS_DIR, filename);
    fs.writeFileSync(filepath, '\ufeff' + csv, 'utf-8');
    res.download(filepath, filename);
});

app.get('/api/files', (req, res) => {
    const files = fs.readdirSync(RESULTS_DIR)
        .filter(f => f.endsWith('.json'))
        .sort().reverse()
        .slice(0, 30)
        .map(f => {
            try {
                const data = JSON.parse(fs.readFileSync(path.join(RESULTS_DIR, f), 'utf-8'));
                return { filename: f, count: Array.isArray(data) ? data.length : 0 };
            } catch (e) { return { filename: f, count: 0 }; }
        });
    res.json(files);
});

app.get('/api/file/:filename', (req, res) => {
    const safe = path.basename(req.params.filename);
    const fp = path.join(RESULTS_DIR, safe);
    if (!fs.existsSync(fp)) return res.status(404).json({ error: 'Файл не найден' });
    res.json(JSON.parse(fs.readFileSync(fp, 'utf-8')));
});

const PORT = 5559;
app.listen(PORT, () => {
    console.log('='.repeat(60));
    console.log('  PHONE CHECKER v4.0');
    console.log(`  Откройте: http://localhost:${PORT}`);
    console.log('='.repeat(60));
});

process.on('uncaughtException', (err) => {
    console.error('[FATAL]', err.message);
});

process.on('unhandledRejection', (err) => {
    console.error('[REJECT]', err);
});

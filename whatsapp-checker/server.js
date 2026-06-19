const express = require('express');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
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

let client = null;
let isReady = false;
let qrCode = null;
let checkResults = [];
let isChecking = false;
let checkProgress = { current: 0, total: 0, status: 'idle' };

function initClient() {
    client = new Client({
        authStrategy: new LocalAuth({ dataPath: path.join(__dirname, '.wwebjs_auth') }),
        puppeteer: {
            headless: true,
            args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
            executablePath: 'C:\\Users\\three\\AppData\\Local\\ms-playwright\\chromium-1223\\chrome-win64\\chrome.exe',
        },
    });

    client.on('qr', (qr) => {
        qrCode = qr;
        isReady = false;
        console.log('QR Code generated.');
        qrcode.generate(qr, { small: true });
    });

    client.on('ready', () => {
        isReady = true;
        qrCode = null;
        console.log('WhatsApp client is ready!');
    });

    client.on('authenticated', () => console.log('Authenticated'));
    client.on('auth_failure', (msg) => { console.error('Auth failure:', msg); isReady = false; });
    client.on('disconnected', (reason) => { console.log('Disconnected:', reason); isReady = false; });

    client.initialize();
}

function validatePhone(phone, defaultCountry = 'MD') {
    try {
        const cleaned = phone.replace(/[\s\-\(\)]/g, '');
        let number;
        if (cleaned.startsWith('+')) {
            number = phoneUtil.parse(cleaned);
        } else {
            number = phoneUtil.parse(cleaned, defaultCountry);
        }
        const valid = phoneUtil.isValidNumber(number);
        const region = phoneUtil.getRegionCodeForNumber(number);
        const countryCode = number.getCountryCode();
        const nationalNumber = number.getNationalNumber();
        const numberType = phoneUtil.getNumberType(number);

        let typeStr = 'UNKNOWN';
        switch (numberType) {
            case 0: typeStr = 'FIXED_LINE'; break;
            case 1: typeStr = 'MOBILE'; break;
            case 2: typeStr = 'FIXED_LINE_OR_MOBILE'; break;
            case 3: typeStr = 'TOLL_FREE'; break;
            case 4: typeStr = 'PREMIUM_RATE'; break;
            case 5: typeStr = 'SHARED_COST'; break;
            case 6: typeStr = 'VOIP'; break;
            case 7: typeStr = 'PERSONAL_NUMBER'; break;
            case 8: typeStr = 'PAGER'; break;
            case 9: typeStr = 'UAN'; break;
            case 10: typeStr = 'VOICEMAIL'; break;
        }

        const formatted = phoneUtil.format(number, PNF.E164);
        const national = phoneUtil.format(number, PNF.NATIONAL);

        return {
            valid,
            country: region || 'Unknown',
            countryCode: '+' + countryCode,
            nationalNumber: String(nationalNumber),
            type: typeStr,
            formatted,
            formattedNational: national,
        };
    } catch (e) {
        return { valid: false, error: e.message };
    }
}

async function checkWhatsApp(phone) {
    if (!client || !isReady) return { status: 'error', error: 'WhatsApp not connected' };
    try {
        const number = phone.replace(/[^0-9]/g, '');
        if (number.length < 7) return { status: 'invalid', registered: false, error: 'Too short' };
        const chatId = `${number}@c.us`;
        const registered = await client.isRegisteredUser(chatId);
        let result = {
            status: registered ? 'registered' : 'not_registered',
            registered,
        };
        if (registered) {
            try { result.profile_pic = await client.getProfilePicUrl(chatId) || null; } catch (e) { result.profile_pic = null; }
            try { const info = await client.getNumberId(number); if (info) result.wid = info._serialized; } catch (e) {}
        }
        return result;
    } catch (e) {
        return { status: 'error', registered: false, error: e.message };
    }
}

function extractPhones(data) {
    const phones = [];
    if (Array.isArray(data)) {
        for (const item of data) {
            if (typeof item === 'string') {
                if (item.includes('+')) {
                    phones.push(...item.split('+').filter(p => p.trim().length >= 7));
                } else {
                    phones.push(item);
                }
                continue;
            }
            const fields = ['phone', 'Phone', 'PHONE', 'number', 'Number', 'tel', 'Tel', 'mobile', 'Mobile', 'whatsapp', 'WhatsApp'];
            for (const f of fields) {
                if (item[f]) {
                    const val = String(item[f]);
                    if (val.includes('+')) {
                        phones.push(...val.split('+').filter(p => p.trim().length >= 7));
                    } else {
                        phones.push(val);
                    }
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
    return phones
        .map(p => p.trim().replace(/[^0-9+]/g, '').replace('+', ''))
        .filter(p => p && p.length >= 7);
}

function extractBusinesses(data) {
    if (!Array.isArray(data)) return [];
    return data.map(item => ({
        name: item.name || '',
        phone: item.phone || '',
        address: item.address || '',
        website: item.website || '',
        city: item.city || '',
        category: item.category || '',
        rating: item.rating || 0,
    }));
}

app.get('/api/status', (req, res) => {
    res.json({ ready: isReady, qr: qrCode, checking: isChecking, progress: checkProgress });
});

app.post('/api/connect', (req, res) => {
    if (client && isReady) return res.json({ ok: true, message: 'Already connected' });
    initClient();
    res.json({ ok: true, message: 'Connecting...' });
});

app.post('/api/validate', (req, res) => {
    const { phone, country } = req.body;
    if (!phone) return res.status(400).json({ error: 'Enter phone number' });
    const result = validatePhone(phone, country || 'MD');
    res.json(result);
});

app.post('/api/check', async (req, res) => {
    const { phone } = req.body;
    if (!phone) return res.status(400).json({ error: 'Enter phone number' });
    if (!isReady) return res.status(400).json({ error: 'WhatsApp not connected' });
    const validation = validatePhone(phone);
    const whatsapp = await checkWhatsApp(phone);
    res.json({ phone, validation, whatsapp });
});

app.post('/api/check-batch', async (req, res) => {
    const { phones, services, country } = req.body;
    if (!phones || !phones.length) return res.status(400).json({ error: 'Enter phone numbers' });
    const checkServices = services || ['validation'];
    if (checkServices.includes('whatsapp') && (!isReady)) {
        return res.status(400).json({ error: 'WhatsApp not connected' });
    }
    if (isChecking) return res.status(400).json({ error: 'Already running' });

    isChecking = true;
    checkResults = [];
    checkProgress = { current: 0, total: phones.length, status: 'starting' };

    const delay = Math.max(100, parseInt(req.body.delay) || 1500);

    (async () => {
        for (let i = 0; i < phones.length; i++) {
            if (!isChecking) break;
            const phone = phones[i].replace(/[^0-9+]/g, '').replace(/^\+/, '');
            if (!phone || phone.length < 7) {
                checkResults.push({ phone: phone || phones[i], validation: { valid: false }, services: {} });
                continue;
            }
            checkProgress = { current: i + 1, total: phones.length, status: `Checking ${i + 1}/${phones.length}`, currentPhone: phone };

            const result = { phone, validation: {}, services: {} };

            if (checkServices.includes('validation')) {
                result.validation = validatePhone(phone, country || 'MD');
            }

            if (checkServices.includes('whatsapp')) {
                result.services.whatsapp = await checkWhatsApp(phone);
                if (i < phones.length - 1 && isChecking) await new Promise(r => setTimeout(r, delay));
            }

            checkResults.push(result);
        }

        const filename = `check_${Date.now()}.json`;
        fs.writeFileSync(path.join(RESULTS_DIR, filename), JSON.stringify(checkResults, null, 2));

        checkProgress = {
            current: phones.length,
            total: phones.length,
            status: 'done',
            totalResults: checkResults.length,
        };
        isChecking = false;
    })();

    res.json({ ok: true, total: phones.length });
});

app.post('/api/upload', upload.single('file'), async (req, res) => {
    if (!req.file) return res.status(400).json({ error: 'No file' });
    try {
        const content = fs.readFileSync(req.file.path, 'utf-8');
        fs.unlinkSync(req.file.path);

        let phones = [];
        let businesses = [];
        const ext = req.file.originalname.split('.').pop().toLowerCase();

        if (ext === 'json') {
            const data = JSON.parse(content);
            phones = extractPhones(data);
            businesses = extractBusinesses(data);
        } else if (ext === 'csv') {
            const lines = content.split('\n');
            for (const line of lines) {
                const parts = line.split(/[,;\t]/);
                for (const part of parts) {
                    const cleaned = part.replace(/[^0-9+]/g, '').replace('+', '');
                    if (cleaned.length >= 7) phones.push(cleaned);
                }
            }
        } else if (ext === 'txt') {
            phones = content.split('\n').map(l => l.replace(/[^0-9+]/g, '').replace('+', '')).filter(l => l.length >= 7);
        }

        phones = [...new Set(phones)];
        res.json({ ok: true, phones, businesses, count: phones.length, filename: req.file.originalname });
    } catch (e) {
        res.status(400).json({ error: 'Failed to parse file: ' + e.message });
    }
});

app.get('/api/check-progress', (req, res) => {
    res.json(checkProgress);
});

app.get('/api/results', (req, res) => {
    res.json(checkResults);
});

app.get('/api/download-results', (req, res) => {
    if (!checkResults.length) return res.status(404).json({ error: 'No results' });
    const filename = `check_${Date.now()}.json`;
    const filepath = path.join(RESULTS_DIR, filename);
    fs.writeFileSync(filepath, JSON.stringify(checkResults, null, 2));
    res.download(filepath, filename);
});

app.get('/api/files', (req, res) => {
    const files = fs.readdirSync(RESULTS_DIR)
        .filter(f => f.endsWith('.json'))
        .sort().reverse()
        .slice(0, 20)
        .map(f => {
            try {
                const data = JSON.parse(fs.readFileSync(path.join(RESULTS_DIR, f), 'utf-8'));
                return { filename: f, count: Array.isArray(data) ? data.length : 0 };
            } catch (e) { return { filename: f, count: 0 }; }
        });
    res.json(files);
});

const PORT = 5559;
app.listen(PORT, () => {
    console.log('='.repeat(60));
    console.log('  PHONE CHECKER v3.0');
    console.log(`  Open: http://localhost:${PORT}`);
    console.log('='.repeat(60));
    initClient();
});

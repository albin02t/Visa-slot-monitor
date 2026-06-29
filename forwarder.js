const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const fs = require('fs');
const path = require('path');

const DATA_DIR = process.env.DATA_DIR || __dirname;

// Config arrives via environment variables (set by the server when it spawns
// this process). Optionally also load a .env from DATA_DIR if one exists, for
// standalone runs. Never crash if it's absent.
const envPath = path.join(DATA_DIR, '.env');
if (fs.existsSync(envPath)) {
    fs.readFileSync(envPath, 'utf-8').split('\n').forEach(line => {
        const [key, ...rest] = line.split('=');
        if (key && rest.length && !process.env[key.trim()]) {
            process.env[key.trim()] = rest.join('=').trim();
        }
    });
}

// The Twilio sandbox number that sends you alerts (without 'whatsapp:' prefix)
const TWILIO_NUMBER = (process.env.TWILIO_WHATSAPP_FROM || '').replace('whatsapp:', '');

// Per-user Twilio sandbox join phrase (e.g. "join lovely-rest"). Each Twilio
// sandbox has its own keyword, so this must come from the user's config.
const SANDBOX_KEYWORD = (process.env.TWILIO_SANDBOX_KEYWORD || '').trim();

// Contacts to forward to — comma-separated, with country code, no + (e.g. "917994741413,919876543210")
const FORWARD_TO = (process.env.FORWARD_TO || '')
    .split(',')
    .map(n => n.trim())
    .filter(Boolean);

if (!TWILIO_NUMBER) {
    console.error('TWILIO_WHATSAPP_FROM not set in .env');
    process.exit(1);i
}
if (FORWARD_TO.length === 0) {
    console.error('FORWARD_TO not set in .env — add comma-separated numbers e.g. FORWARD_TO=917994741413,919876543210');
    process.exit(1);
}

console.log(`Forwarding messages from ${TWILIO_NUMBER} to: ${FORWARD_TO.join(', ')}`);

// An ungraceful exit (e.g. container restart) can leave Chromium "Singleton"
// lock files in the profile dir, which make the next launch fail with
// "browser is already running". Clear them before starting.
function clearStaleLocks(root) {
    let removed = 0;
    const walk = dir => {
        let entries = [];
        try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
        for (const e of entries) {
            const p = path.join(dir, e.name);
            if (e.isDirectory()) walk(p);
            else if (e.name.startsWith('Singleton')) { try { fs.unlinkSync(p); removed++; } catch {} }
        }
    };
    walk(root);
    if (removed) console.log(`Cleared ${removed} stale Chromium lock file(s)`);
}
clearStaleLocks(path.join(DATA_DIR, '.wwebjs_auth'));

const client = new Client({
    authStrategy: new LocalAuth({ dataPath: path.join(DATA_DIR, '.wwebjs_auth') }),
    puppeteer: {
        executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
        args: ['--no-sandbox', '--disable-setuid-sandbox'],
    },
});

process.on('uncaughtException', err => {
    console.error('Forwarder fatal error:', err.message);
    process.exit(1);
});

client.on('qr', qr => {
    console.log('\nScan this QR code with your WhatsApp:\n');
    qrcode.generate(qr, { small: true });
    // Machine-readable marker so the web UI can render a clean, scannable QR.
    console.log('WWEBJS_QR ' + qr);
});

let TWILIO_CHAT_ID = null;

client.on('ready', async () => {
    console.log('WhatsApp Web connected — listening for messages from Twilio...');
    try {
        const contact = await client.getContactById(`${TWILIO_NUMBER.replace('+', '')}@c.us`);
        TWILIO_CHAT_ID = contact.id._serialized;
        console.log(`Resolved Twilio chat ID: ${TWILIO_CHAT_ID}`);
    } catch (e) {
        console.warn('Could not resolve Twilio contact, will match on message content instead:', e.message);
    }
});

client.on('message', async msg => {
    const contact = await msg.getContact();
    const senderNumber = contact.id.user;
    const expectedNumber = TWILIO_NUMBER.replace('+', '');
    console.log(`[DEBUG] contact.id.user=${senderNumber} | expected=${expectedNumber}`);

    if (senderNumber !== expectedNumber) {
        console.log(`[DEBUG] Ignored — not from Twilio`);
        return;
    }

    console.log(`[DEBUG] Matched — forwarding`);

    console.log(`[${new Date().toISOString()}] Received from Twilio: ${msg.body.slice(0, 80)}`);

    for (const number of FORWARD_TO) {
        try {
            const chatId = `${number.replace('+', '')}@c.us`;
            await client.sendMessage(chatId, msg.body);
            console.log(`Forwarded to ${number}`);
        } catch (err) {
            console.error(`Failed to forward to ${number}:`, err.message);
        }
    }
});

function scheduleDailyKeepalive() {
    const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;
    const nowUtc = Date.now();
    const nowIst = new Date(nowUtc + IST_OFFSET_MS);

    const nextTarget = new Date(nowIst);
    nextTarget.setHours(8, 0, 0, 0);
    if (nowIst >= nextTarget) nextTarget.setDate(nextTarget.getDate() + 1);

    const delayMs = nextTarget - nowIst;
    if (!SANDBOX_KEYWORD) {
        console.log('Keepalive: TWILIO_SANDBOX_KEYWORD not set — skipping daily keepalive');
        return;
    }
    console.log(`Keepalive: next '${SANDBOX_KEYWORD}' in ${Math.round(delayMs / 1000)}s (at 08:00 IST)`);

    setTimeout(async () => {
        const twilioChat = `${TWILIO_NUMBER.replace('+', '')}@c.us`;
        try {
            await client.sendMessage(twilioChat, SANDBOX_KEYWORD);
            await new Promise(r => setTimeout(r, 5000));
            console.log(`[${new Date().toISOString()}] Keepalive: sent '${SANDBOX_KEYWORD}' to Twilio sandbox`);
        } catch (err) {
            console.error('Keepalive send failed:', err.message);
        }
        scheduleDailyKeepalive();
    }, delayMs);
}

client.on('ready', () => scheduleDailyKeepalive());

client.on('auth_failure', () => console.error('WhatsApp auth failed — delete .wwebjs_auth and try again'));
client.on('disconnected', reason => console.warn('Disconnected:', reason));

client.initialize();

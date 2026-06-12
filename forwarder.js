const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const fs = require('fs');
const path = require('path');

// Load .env manually (no dotenv dependency needed)
const envPath = path.join(__dirname, '.env');
fs.readFileSync(envPath, 'utf-8').split('\n').forEach(line => {
    const [key, ...rest] = line.split('=');
    if (key && rest.length) process.env[key.trim()] = rest.join('=').trim();
});

// The Twilio sandbox number that sends you alerts (without 'whatsapp:' prefix)
const TWILIO_NUMBER = (process.env.TWILIO_WHATSAPP_FROM || '').replace('whatsapp:', '');

// Contacts to forward to — comma-separated, with country code, no + (e.g. "917994741413,919876543210")
const FORWARD_TO = (process.env.FORWARD_TO || '')
    .split(',')
    .map(n => n.trim())
    .filter(Boolean);

if (!TWILIO_NUMBER) {
    console.error('TWILIO_WHATSAPP_FROM not set in .env');
    process.exit(1);
}
if (FORWARD_TO.length === 0) {
    console.error('FORWARD_TO not set in .env — add comma-separated numbers e.g. FORWARD_TO=917994741413,919876543210');
    process.exit(1);
}

console.log(`Forwarding messages from ${TWILIO_NUMBER} to: ${FORWARD_TO.join(', ')}`);

const client = new Client({
    authStrategy: new LocalAuth({ dataPath: '.wwebjs_auth' }),
    puppeteer: { args: ['--no-sandbox', '--disable-setuid-sandbox'] }
});

client.on('qr', qr => {
    console.log('\nScan this QR code with your WhatsApp:\n');
    qrcode.generate(qr, { small: true });
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

client.on('auth_failure', () => console.error('WhatsApp auth failed — delete .wwebjs_auth and try again'));
client.on('disconnected', reason => console.warn('Disconnected:', reason));

client.initialize();

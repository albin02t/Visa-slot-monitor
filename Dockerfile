FROM node:20-slim

# --- System deps: Python, Chromium (for whatsapp-web.js/puppeteer) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        chromium fonts-liberation libglib2.0-0 libnss3 libdbus-1-3 \
        libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Python deps ---
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir --break-system-packages -r requirements.txt

# --- Node deps ---
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# --- App code ---
COPY . .

ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
ENV DATA_DIR=/data
EXPOSE 8000

CMD ["python3", "-m", "uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8000"]

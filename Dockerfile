FROM node:20-slim

# Install Python and system deps
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    chromium \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libgdk-pixbuf2.0-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    xdg-utils \
    wget \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Install Python deps for all services
COPY web-scraper/requirements.txt /tmp/web-scraper-requirements.txt
COPY email-sender/requirements.txt /tmp/email-sender-requirements.txt
COPY text-uniquifier/requirements.txt /tmp/text-uniquifier-requirements.txt
COPY google-maps-scraper/requirements.txt /tmp/google-maps-scraper-requirements.txt

RUN pip3 install --break-system-packages \
    -r /tmp/web-scraper-requirements.txt \
    -r /tmp/email-sender-requirements.txt \
    -r /tmp/text-uniquifier-requirements.txt \
    -r /tmp/google-maps-scraper-requirements.txt

RUN playwright install chromium

# Install Node.js deps
COPY gateway/package.json /app/gateway/
RUN cd /app/gateway && npm install

COPY package.json /app/
RUN cd /app && npm install

COPY whatsapp-checker/package.json /app/whatsapp-checker/
RUN cd /app/whatsapp-checker && npm install

# Copy all source files
COPY . /app/

# Make start script executable
RUN chmod +x /app/start.sh

EXPOSE 3000

CMD ["/app/start.sh"]

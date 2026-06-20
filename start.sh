#!/bin/bash
set -e

echo "Starting Pintrion Pantheon services..."

# Start Python services
echo "Starting Web Scraper on port 5555..."
cd /app/web-scraper && python3 app.py &

echo "Starting Email Sender on port 5556..."
cd /app/email-sender && python3 app.py &

echo "Starting Text Uniquifier on port 5557..."
cd /app/text-uniquifier && python3 app.py &

echo "Starting Google Maps Scraper on port 5558..."
cd /app/google-maps-scraper && python3 app.py &

# Start Node.js services
echo "Starting WhatsApp Checker on port 5559..."
cd /app/whatsapp-checker && node server.js &

echo "Starting Dashboard on port 10000..."
cd /app/dashboard && node server.js &

# Start Gateway (wait a moment for services to start)
sleep 3
echo "Starting Gateway on port ${PORT:-10000}..."
cd /app/gateway && node server.js &

echo "All services started!"

# Wait for all background processes
wait

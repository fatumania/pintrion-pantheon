const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const path = require('path');
const app = express();

const PORT = process.env.PORT || 3000;

const services = [
  { path: '/web-scraper', target: 'http://127.0.0.1:5555' },
  { path: '/email-sender', target: 'http://127.0.0.1:5556' },
  { path: '/text-uniquifier', target: 'http://127.0.0.1:5557' },
  { path: '/google-maps-scraper', target: 'http://127.0.0.1:5558' },
  { path: '/phone-checker', target: 'http://127.0.0.1:5559' },
];

services.forEach(({ path: svcPath, target }) => {
  app.use(svcPath, createProxyMiddleware({
    target,
    changeOrigin: true,
    pathRewrite: (pathStr) => pathStr.replace(new RegExp(`^${svcPath}`), ''),
    onError(err, req, res) {
      console.error(`Proxy error for ${svcPath}:`, err.message);
      res.status(502).json({ error: 'Service unavailable', path: svcPath });
    },
  }));
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok', services: services.map(s => s.path), timestamp: new Date().toISOString() });
});

app.use('/', express.static(path.join(__dirname, '..', 'dashboard', 'public')));

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Gateway listening on 0.0.0.0:${PORT}`);
});

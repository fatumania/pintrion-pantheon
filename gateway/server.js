const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const app = express();

const PORT = process.env.PORT || 10000;

const services = [
  { path: '/web-scraper', target: 'http://127.0.0.1:5555' },
  { path: '/email-sender', target: 'http://127.0.0.1:5556' },
  { path: '/text-uniquifier', target: 'http://127.0.0.1:5557' },
  { path: '/google-maps-scraper', target: 'http://127.0.0.1:5558' },
  { path: '/phone-checker', target: 'http://127.0.0.1:5559' },
];

services.forEach(({ path, target }) => {
  app.use(path, createProxyMiddleware({
    target,
    changeOrigin: true,
    pathRewrite: (pathStr) => pathStr.replace(new RegExp(`^${path}`), ''),
    onError(err, req, res) {
      console.error(`Proxy error for ${path}:`, err.message);
      res.status(502).json({ error: 'Service unavailable', path });
    },
  }));
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

app.use('/', createProxyMiddleware({
  target: 'http://127.0.0.1:10000',
  changeOrigin: true,
  onError(err, req, res) {
    console.error('Dashboard proxy error:', err.message);
    res.status(502).json({ error: 'Dashboard unavailable' });
  },
}));

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Gateway listening on 0.0.0.0:${PORT}`);
});

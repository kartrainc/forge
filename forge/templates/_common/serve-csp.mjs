// Serves the production build (dist/) the way nginx.conf does under
// security.csp: every path without a file gets index.html, with the policy
// nginx.conf sends, a fresh nonce per response and the __CSP_NONCE__
// placeholder replaced. A report-uri is appended to the policy; each violation
// the browser reports is appended to the file FORGE_CSP_REPORT names, one JSON
// object per line, or printed when it is unset.
//
//   VITE_AUTH_DISABLED=true npm run build && npx playwright test -c playwright.csp.config.ts
import { randomBytes } from 'node:crypto';
import { appendFileSync, readFileSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import path from 'node:path';
import process from 'node:process';
import { URL } from 'node:url';

const root = path.resolve('dist');
const port = Number(process.argv[2] || 4174);
const reports = process.env.FORGE_CSP_REPORT;
const match = readFileSync('nginx.conf', 'utf8').match(/add_header Content-Security-Policy "([^"]+)"/);
if (!match) throw new Error('nginx.conf sends no Content-Security-Policy; is security.csp on?');
const policy = match[1];
const page = readFileSync(path.join(root, 'index.html'), 'utf8');
const types = {
  '.css': 'text/css', '.html': 'text/html', '.ico': 'image/x-icon', '.js': 'text/javascript',
  '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2',
};

function asset(pathname) {
  const candidate = path.join(root, decodeURIComponent(pathname));
  if (!candidate.startsWith(root + path.sep)) return null;
  try {
    return statSync(candidate).isFile() ? candidate : null;
  } catch {
    return null;
  }
}

createServer((request, response) => {
  const { pathname } = new URL(request.url, 'http://localhost');
  if (pathname === '/__csp-report') {
    let body = '';
    request.on('data', chunk => (body += chunk));
    request.on('end', () => {
      const parsed = JSON.parse(body);
      const report = JSON.stringify(parsed['csp-report'] ?? parsed);
      if (reports) appendFileSync(reports, report + '\n');
      else process.stderr.write(`Content-Security-Policy violation: ${report}\n`);
      response.writeHead(204).end();
    });
    return;
  }
  const file = pathname === '/index.html' ? null : asset(pathname);
  if (file) {
    response.writeHead(200, { 'Content-Type': types[path.extname(file)] ?? 'application/octet-stream' });
    response.end(readFileSync(file));
    return;
  }
  if (pathname.startsWith('/api/')) {
    response.writeHead(404, { 'Content-Type': 'application/json' }).end('{"message":"Not found"}');
    return;
  }
  const nonce = randomBytes(16).toString('hex');
  response.writeHead(200, {
    'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'no-store',
    'Content-Security-Policy': `${policy.replaceAll('$request_id', nonce)}; report-uri /__csp-report`,
  });
  response.end(page.replaceAll('__CSP_NONCE__', nonce));
}).listen(port, '127.0.0.1');

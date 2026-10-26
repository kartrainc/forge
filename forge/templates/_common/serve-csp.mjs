// Serves the production build (dist/) the way nginx.conf does under
// security.csp: every path without a file gets index.html, with the policy
// nginx.conf sends, a fresh nonce per response and the __CSP_NONCE__
// placeholder replaced.
//
// The policy is the `add_header Content-Security-Policy` of nginx.conf. A
// project that keeps its policy in a file of its own (a header template the
// image renders, say) names that file in csp-check.json beside this script's
// Playwright config, with the values of the template's ${NAME} variables:
//
//   { "policy": "security-headers.conf", "variables": { "MEDIA_ORIGINS": "https://media.example" } }
//
// A variable the file uses and csp-check.json does not declare is an error, as
// are a source with no policy or with two different ones. $request_id and other
// nginx variables are not ${NAME} variables and stay as they are.
//
// The policy's own report directives are replaced by one naming /__csp-report:
// a browser that knows report-to ignores report-uri beside it. Each violation
// the browser reports is appended to the file FORGE_CSP_REPORT names, one JSON
// object per line, or printed when it is unset. Until the first report
// arrives, every page also carries a canary: a script from a host that cannot
// resolve, which any working policy blocks, so its report proves that reports
// reach the check (forge.quality.csp fails without one).
//
//   npm run build && npx playwright test -c playwright.csp.config.ts
import { randomBytes } from 'node:crypto';
import { appendFileSync, existsSync, readFileSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import path from 'node:path';
import process from 'node:process';
import { URL } from 'node:url';

const root = path.resolve('dist');
const port = Number(process.argv[2] || 4174);
const reports = process.env.FORGE_CSP_REPORT;
// The canary's address; forge.quality.csp.CANARY names the same one.
const CANARY = 'https://csp-canary.invalid/forge.js';

function policies(text, source) {
  const found = [...new Set([...text.matchAll(/add_header Content-Security-Policy "([^"]+)"/g)].map(m => m[1]))];
  if (found.length > 1) throw new Error(`${source} sends ${found.length} different Content-Security-Policy headers`);
  return found[0];
}

function servedPolicy() {
  const inline = policies(readFileSync('nginx.conf', 'utf8'), 'nginx.conf');
  if (inline) return inline;
  if (!existsSync('csp-check.json')) {
    throw new Error('nginx.conf sends no Content-Security-Policy and csp-check.json names no policy file; is security.csp on?');
  }
  const { policy: file, variables = {} } = JSON.parse(readFileSync('csp-check.json', 'utf8'));
  if (typeof file !== 'string' || !existsSync(file)) throw new Error(`csp-check.json: policy file ${file} not found`);
  const text = policies(readFileSync(file, 'utf8'), file);
  if (!text) throw new Error(`${file} sends no Content-Security-Policy`);
  return text.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, name) => {
    if (!Object.hasOwn(variables, name)) throw new Error(`${file} uses \${${name}}, which csp-check.json does not declare`);
    return String(variables[name]);
  });
}

const policy = servedPolicy()
  .split(';')
  .map(directive => directive.trim().replace(/\s+/g, ' '))
  .filter(directive => directive && !/^report-(uri|to)\b/.test(directive))
  .join('; ');
const page = readFileSync(path.join(root, 'index.html'), 'utf8');
const types = {
  '.css': 'text/css', '.html': 'text/html', '.ico': 'image/x-icon', '.js': 'text/javascript',
  '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2',
};
let canaryReported = false;

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
      const report = parsed['csp-report'] ?? parsed;
      if (String(report['blocked-uri'] ?? '').startsWith(CANARY)) canaryReported = true;
      if (reports) appendFileSync(reports, JSON.stringify(report) + '\n');
      else process.stderr.write(`Content-Security-Policy violation: ${JSON.stringify(report)}\n`);
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
  const html = page.replaceAll('__CSP_NONCE__', nonce);
  response.end(canaryReported ? html : html.replace('</head>', `<script src="${CANARY}"></script></head>`));
}).listen(port, '127.0.0.1');

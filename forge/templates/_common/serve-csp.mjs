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
// Exactly one source names the policy: both nginx.conf and csp-check.json, or
// neither, is an error, as are a variable the file uses and csp-check.json does
// not declare, and a source with two different policies. $request_id and other
// nginx variables are not ${NAME} variables and stay as they are.
//
// The policy's own report directives are replaced by one naming /__csp-report:
// a browser that knows report-to ignores report-uri beside it. Each violation
// the browser reports is appended to the file FORGE_CSP_REPORT names, one JSON
// object per line, or printed when it is unset.
//
// With FORGE_CSP_REPORT set (forge.quality.csp sets it), the script first
// proves that reports reach that file: it opens /__csp-canary once in the
// project's Playwright Chromium. That page has a fixed policy of its own,
// script-src 'none', and a script, so it is reported whatever the project's
// policy admits; the app's pages are served untouched. The check fails without
// the canary's report.
//
//   npm run build && npx playwright test -c playwright.csp.config.ts
import { randomBytes } from 'node:crypto';
import { appendFileSync, existsSync, readFileSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';
import process from 'node:process';
import { URL } from 'node:url';

const root = path.resolve('dist');
const port = Number(process.argv[2] || 4174);
const reports = process.env.FORGE_CSP_REPORT;
// The canary's script; forge.quality.csp.CANARY names the same path.
const CANARY_SCRIPT = '/__csp-canary.js';
const CANARY_PAGE = `<!doctype html><html><head><script src="${CANARY_SCRIPT}"></script></head><body></body></html>`;
const CANARY_POLICY = "default-src 'none'; script-src 'none'; report-uri /__csp-report";

function policies(text, source) {
  const found = [...new Set([...text.matchAll(/add_header Content-Security-Policy "([^"]+)"/g)].map(m => m[1]))];
  if (found.length > 1) throw new Error(`${source} sends ${found.length} different Content-Security-Policy headers`);
  return found[0];
}

function fromCheckFile() {
  const { policy: file, variables = {} } = JSON.parse(readFileSync('csp-check.json', 'utf8'));
  if (typeof file !== 'string' || !existsSync(file)) throw new Error(`csp-check.json: policy file ${file} not found`);
  const text = policies(readFileSync(file, 'utf8'), file);
  if (!text) throw new Error(`${file} sends no Content-Security-Policy`);
  return text.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, name) => {
    if (!Object.hasOwn(variables, name)) throw new Error(`${file} uses \${${name}}, which csp-check.json does not declare`);
    return String(variables[name]);
  });
}

function servedPolicy() {
  const inline = existsSync('nginx.conf') ? policies(readFileSync('nginx.conf', 'utf8'), 'nginx.conf') : undefined;
  const named = existsSync('csp-check.json');
  if (inline && named) {
    throw new Error('nginx.conf sends a Content-Security-Policy and csp-check.json names another source: keep one');
  }
  if (inline) return inline;
  if (named) return fromCheckFile();
  throw new Error('neither nginx.conf nor a file csp-check.json names sends a Content-Security-Policy; is security.csp on?');
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

const server = createServer((request, response) => {
  const { pathname } = new URL(request.url, 'http://localhost');
  if (pathname === '/__csp-report') {
    let body = '';
    request.on('data', chunk => (body += chunk));
    request.on('end', () => {
      const parsed = JSON.parse(body);
      const report = parsed['csp-report'] ?? parsed;
      if (String(report['blocked-uri'] ?? '').endsWith(CANARY_SCRIPT)) canaryReported = true;
      if (reports) appendFileSync(reports, JSON.stringify(report) + '\n');
      else process.stderr.write(`Content-Security-Policy violation: ${JSON.stringify(report)}\n`);
      response.writeHead(204).end();
    });
    return;
  }
  if (pathname === '/__csp-canary') {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': CANARY_POLICY });
    response.end(CANARY_PAGE);
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
});

// Open the canary once in a browser and wait for its report.
async function provesReporting() {
  let browser;
  try {
    const { chromium } = await import('@playwright/test');
    browser = await chromium.launch();
    const tab = await browser.newPage();
    await tab.goto(`http://127.0.0.1:${port}/__csp-canary`);
    for (let waited = 0; !canaryReported && waited < 15_000; waited += 100) {
      await sleep(100);
    }
    if (!canaryReported) process.stderr.write('serve-csp: the canary page sent no report within 15 s\n');
  } catch (error) {
    process.stderr.write(`serve-csp: could not open the canary page in Chromium: ${error.message}\n`);
  } finally {
    await browser?.close();
  }
}

server.listen(port, '127.0.0.1', () => {
  if (reports) provesReporting();
});

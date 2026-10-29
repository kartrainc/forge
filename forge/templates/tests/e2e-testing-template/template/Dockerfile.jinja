FROM node:26-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
RUN npx playwright install --with-deps chromium

COPY . .

# Ensure app.localhost resolves to 127.0.0.1 (Linux doesn't auto-resolve *.localhost)
ENTRYPOINT ["/bin/sh", "-c", "echo '127.0.0.1 app.localhost' >> /etc/hosts && echo '127.0.0.1 localhost' >> /etc/hosts && exec \"$@\"", "--"]
CMD ["npx", "playwright", "test"]

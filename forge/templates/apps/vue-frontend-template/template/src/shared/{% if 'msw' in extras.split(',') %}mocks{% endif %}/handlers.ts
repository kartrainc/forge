import { http, HttpResponse } from 'msw'

export const infoHandlers = [
  http.get('*/api/v1/info', () =>
    HttpResponse.json({
      title: 'My Service',
      version: '0.1.0',
      description: 'A microservice template',
    }),
  ),
]

export const healthHandlers = [
  http.get('*/api/v1/health/live', () =>
    HttpResponse.json({ status: 'UP', details: 'Service is running' }),
  ),

  http.get('*/api/v1/health/ready', () =>
    HttpResponse.json({
      status: 'UP',
      components: {
        database: { status: 'UP', latency_ms: 2.5, details: null },
      },
      system_info: {
        platform: 'Node.js',
      },
    }),
  ),
]

// --- feature handler imports ---
// --- end feature handler imports ---

export const handlers = [
  ...infoHandlers,
  ...healthHandlers,
  // --- feature handlers ---
  // --- end feature handlers ---
]

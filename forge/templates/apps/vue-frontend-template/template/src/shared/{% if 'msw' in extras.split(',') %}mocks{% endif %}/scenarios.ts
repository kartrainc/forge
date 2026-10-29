import { http, HttpResponse, delay } from 'msw'

export const errorScenarios = {
  items500: http.get('*/api/v1/items', () =>
    HttpResponse.json(
      { message: 'Internal server error', type: 'ServerError', detail: null },
      { status: 500 },
    ),
  ),
  items401: http.get('*/api/v1/items', () =>
    HttpResponse.json(
      { message: 'Unauthorized', type: 'AuthError', detail: null },
      { status: 401 },
    ),
  ),
  itemsSlow: http.get('*/api/v1/items', async () => {
    await delay(3000)
    return HttpResponse.json({
      items: [],
      total: 0,
      skip: 0,
      limit: 50,
      has_more: false,
    })
  }),
}

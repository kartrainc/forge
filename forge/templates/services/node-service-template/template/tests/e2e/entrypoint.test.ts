import { createServer } from "node:net";
import { afterEach, expect, it, vi } from "vitest";

async function freePort(): Promise<number> {
  const server = createServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as { port: number };
  await new Promise<void>((resolve) => server.close(() => resolve()));
  return port;
}

afterEach(() => {
  vi.restoreAllMocks();
});

it("listens on the configured port and drains on SIGTERM", async () => {
  const port = await freePort();
  process.env.APP__SERVER__HOST = "127.0.0.1";
  process.env.APP__SERVER__PORT = String(port);
  const exited = new Promise<number | undefined>((resolve) => {
    vi.spyOn(process, "exit").mockImplementation(((code?: number) => {
      resolve(code);
    }) as typeof process.exit);
  });
  const listening = new Promise<void>((resolve) => {
    vi.spyOn(console, "log").mockImplementation((message: unknown) => {
      if (String(message).startsWith("Server running on")) {
        resolve();
      }
    });
  });

  await import("../../src/index.js");
  await listening;
  const response = await fetch(`http://127.0.0.1:${port}/api/v1/health/live`);
  expect(response.status).toBe(200);

  process.emit("SIGTERM");
  expect(await exited).toBe(0);
  await expect(fetch(`http://127.0.0.1:${port}/api/v1/health/live`)).rejects.toThrow();
});

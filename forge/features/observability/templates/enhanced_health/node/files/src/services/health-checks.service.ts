/**
 * Supplemental readiness checks for downstream services.
 *
 * Best-effort: a missing optional dep (redis, node-fetch in older runtimes)
 * surfaces as DOWN rather than crashing the route. Wire these into a custom
 * `/health/ready` handler or mount `/health/deep` (default) alongside it.
 */

export interface CheckResult {
	status: "up" | "down";
	latency_ms: number;
	error: string | null;
}

export async function checkRedis(url?: string): Promise<CheckResult> {
	const resolvedUrl = url ?? process.env.REDIS_URL ?? "redis://redis:6379/0";
	const start = performance.now();
	let redisModule: typeof import("redis");
	try {
		// Lazy import so node services not using Redis don't pay the load cost.
		redisModule = await import("redis");
	} catch (err) {
		return {
			status: "down",
			latency_ms: 0,
			error: "redis package not installed",
		};
	}
	// One attempt within 2 s, as the Python, Rust and Go checks make. The client's default reconnects
	// without end, so an unreachable Redis would hold every request open and leave a client behind.
	const client = redisModule.createClient({
		url: resolvedUrl,
		socket: { connectTimeout: 2000, reconnectStrategy: false },
	});
	client.on("error", () => {
		// swallow — we handle failure via the promise below
	});
	const attempt = client.connect().then(() => client.ping());
	attempt.catch(() => undefined);
	let timeout: NodeJS.Timeout | undefined;
	const deadline = new Promise<never>((_resolve, reject) => {
		timeout = setTimeout(() => reject(new Error("no answer within 2000 ms")), 2000);
	});
	try {
		const pong = await Promise.race([attempt, deadline]);
		return {
			status: pong ? "up" : "down",
			latency_ms: Math.round(performance.now() - start),
			error: pong ? null : "no PONG",
		};
	} catch (err: any) {
		return {
			status: "down",
			latency_ms: Math.round(performance.now() - start),
			error: String(err?.message ?? err),
		};
	} finally {
		clearTimeout(timeout);
		client.destroy();
	}
}

export async function checkKeycloak(url?: string): Promise<CheckResult> {
	const resolvedUrl =
		url ?? process.env.KEYCLOAK_HEALTH_URL ?? "http://keycloak:9000/health/ready";
	const start = performance.now();
	try {
		const controller = new AbortController();
		const timeout = setTimeout(() => controller.abort(), 3000);
		try {
			const resp = await fetch(resolvedUrl, { signal: controller.signal });
			return {
				status: resp.ok ? "up" : "down",
				latency_ms: Math.round(performance.now() - start),
				error: resp.ok ? null : `http ${resp.status}`,
			};
		} finally {
			clearTimeout(timeout);
		}
	} catch (err: any) {
		return {
			status: "down",
			latency_ms: Math.round(performance.now() - start),
			error: String(err?.message ?? err),
		};
	}
}

/**
 * E.1.b runtime wiring — assert the central error middleware routes
 * ``AppError`` through ``DefaultErrorPort.serialize`` (or the inline
 * fallback when the fragment is disabled). Either path emits the
 * RFC-007 envelope ``{ error: { code, message, type, context,
 * correlation_id } }`` with the correct HTTP status and a
 * round-tripped ``X-Correlation-Id``.
 */
import Fastify, { type FastifyInstance } from "fastify";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { z } from "zod";
import { errorHandler } from "../../src/middleware/error-handler.js";
import {
	AppError,
	NotFoundError,
	ValidationError,
	statusForCode,
} from "../../src/lib/errors.js";

const RFC007_FIELDS = [
	"code",
	"message",
	"type",
	"context",
	"correlation_id",
] as const;

interface ErrorBody {
	code: string;
	message: string;
	type: string;
	context: Record<string, unknown>;
	correlation_id: string;
}

async function buildTestApp(): Promise<FastifyInstance> {
	const app = Fastify({ logger: false });
	app.setErrorHandler(errorHandler);

	app.get("/missing", async () => {
		throw new NotFoundError("Item", "abc-123");
	});

	app.get("/invalid", async () => {
		throw new ValidationError("email is required", { field: "email" });
	});

	app.get("/custom", async () => {
		throw new AppError("READ_ONLY", "Template is read-only", {
			context: { resource: "Template", id: "tmpl-1" },
		});
	});

	app.get("/boom", async () => {
		throw new Error("kaboom");
	});

	app.get("/unparsed", async () => {
		z.object({ email: z.email() }).parse({ email: "not-an-address" });
	});

	return app;
}

describe("E.1.b error-handler runtime wiring", () => {
	let app: FastifyInstance;

	beforeEach(async () => {
		app = await buildTestApp();
		await app.ready();
	});

	afterEach(async () => {
		await app.close();
	});

	it("emits the RFC-007 envelope EXACTLY for AppError sub-classes", async () => {
		const correlationId = "test-corr-xyz-42";
		const response = await app.inject({
			method: "GET",
			url: "/missing",
			headers: { "x-correlation-id": correlationId },
		});

		expect(response.statusCode).toBe(404);
		const body = JSON.parse(response.payload) as { error: ErrorBody };
		expect(Object.keys(body)).toEqual(["error"]);
		expect(Object.keys(body.error).sort()).toEqual([...RFC007_FIELDS].sort());
		expect(body.error.code).toBe("NOT_FOUND");
		expect(body.error.type).toBe("NotFoundError");
		expect(body.error.context).toEqual({ entity: "Item", id: "abc-123" });
		expect(body.error.correlation_id).toBe(correlationId);
		expect(body.error.message).toContain("abc-123");
	});

	it("round-trips correlation_id from X-Correlation-Id", async () => {
		const response = await app.inject({
			method: "GET",
			url: "/invalid",
			headers: { "x-correlation-id": "corr-validation-7" },
		});

		expect(response.statusCode).toBe(422);
		const body = JSON.parse(response.payload) as { error: ErrorBody };
		expect(body.error.code).toBe("VALIDATION_FAILED");
		expect(body.error.context).toEqual({ field: "email" });
		expect(body.error.correlation_id).toBe("corr-validation-7");
	});

	it("matches statusForCode for the canonical mapping", async () => {
		const response = await app.inject({ method: "GET", url: "/custom" });
		expect(response.statusCode).toBe(statusForCode("READ_ONLY"));
		expect(response.statusCode).toBe(403);

		const body = JSON.parse(response.payload) as { error: ErrorBody };
		expect(body.error.code).toBe("READ_ONLY");
		expect(body.error.context).toEqual({ resource: "Template", id: "tmpl-1" });
	});

	it("never leaks the raw exception message for unknown errors", async () => {
		const response = await app.inject({ method: "GET", url: "/boom" });

		expect(response.statusCode).toBe(500);
		const body = JSON.parse(response.payload) as { error: ErrorBody };
		expect(body.error.code).toBe("INTERNAL_ERROR");
		// The original ``kaboom`` text would leak operator-side state; the
		// handler MUST scrub it through the inline 500 path. Correlation
		// id is what operators use to find the real exception in logs.
		expect(body.error.message).not.toContain("kaboom");
		expect(body.error.message).toBe("An unexpected error occurred");
	});

	it("answers a schema failure 422 with every issue's path", async () => {
		const response = await app.inject({ method: "GET", url: "/unparsed" });
		const body = JSON.parse(response.payload) as { error: ErrorBody };

		expect(response.statusCode).toBe(422);
		expect(body.error.code).toBe("VALIDATION_FAILED");
		expect(body.error.context.errors).toEqual([
			expect.objectContaining({ path: ["email"] }),
		]);
	});

	it("preserves the RFC-007 shape across every reachable handler path", async () => {
		for (const url of ["/missing", "/invalid", "/custom", "/boom", "/unparsed"]) {
			const response = await app.inject({ method: "GET", url });
			const body = JSON.parse(response.payload) as { error: ErrorBody };
			expect(Object.keys(body)).toEqual(["error"]);
			expect(Object.keys(body.error).sort()).toEqual(
				[...RFC007_FIELDS].sort(),
			);
		}
	});
});

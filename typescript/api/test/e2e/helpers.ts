// Shared helpers for the TypeScript SDK e2e suite.

import { execSync } from "node:child_process";

/** ENDPOINT host:port the controller is reachable at, e.g.
 * "grpc.jumpstarter.192.168.1.65.nip.io:8082". Set by the e2e shell
 * harness (sourced from $REPO_ROOT/.e2e-setup-complete). */
export function endpoint(): string {
  const v = process.env.ENDPOINT ?? "";
  if (!v) {
    throw new Error(
      "ENDPOINT not set — run via `make e2e-typescript-api` or `source ./.e2e-setup-complete && npm run test:e2e` from typescript/api/",
    );
  }
  return v;
}

/** baseUrl is "https://<ENDPOINT>" — passed to AdminXService constructors. */
export function baseUrl(): string {
  return `https://${endpoint()}`;
}

/** namespace returns the operator-watched namespace (jumpstarter-lab by default). */
export function namespace(): string {
  return process.env.E2E_TEST_NS ?? "jumpstarter-lab";
}

export const FOREIGN_NAMESPACE = "e2e-foreign";

/** uniqueId returns a short, kube-name-safe id with the given prefix. */
export function uniqueId(prefix: string): string {
  const ts = Date.now().toString(36);
  const rand = Math.floor(Math.random() * 1000)
    .toString()
    .padStart(3, "0");
  return `${prefix}-${ts}${rand}`.toLowerCase();
}

/** clientName / exporterName / leaseName / webhookName build the
 * fully-qualified resource identifier the admin REST API uses. */
export function clientName(ns: string, id: string): string {
  return `namespaces/${ns}/clients/${id}`;
}
export function exporterName(ns: string, id: string): string {
  return `namespaces/${ns}/exporters/${id}`;
}
export function leaseName(ns: string, id: string): string {
  return `namespaces/${ns}/leases/${id}`;
}
export function webhookName(ns: string, id: string): string {
  return `namespaces/${ns}/webhooks/${id}`;
}

export function nsParent(ns: string): string {
  return `namespaces/${ns}`;
}

/** rpcStatus is the shape of the JSON body grpc-gateway emits for
 * non-2xx responses. openapi-fetch puts this on `error` when the
 * response status is not OK. */
export interface RpcStatus {
  code?: number;
  message?: string;
  details?: unknown[];
}

/** GRPC codes we assert against in negative tests.
 * https://github.com/grpc/grpc-go/blob/master/codes/codes.go */
export const GRPC = {
  OK: 0,
  CANCELLED: 1,
  UNKNOWN: 2,
  INVALID_ARGUMENT: 3,
  NOT_FOUND: 5,
  ALREADY_EXISTS: 6,
  PERMISSION_DENIED: 7,
  UNAUTHENTICATED: 16,
} as const;

/** expectStatus runs `fn` and returns either the resolved value (on
 * success) or, on rejection, the parsed RpcStatus. Useful for tests
 * that need to inspect the deny code without relying on Vitest's
 * `expect(...).rejects` matchers (those assume an Error subclass). */
export async function captureRpc<T>(
  fn: () => Promise<T>,
): Promise<{ ok: true; data: T } | { ok: false; status: RpcStatus }> {
  try {
    return { ok: true, data: await fn() };
  } catch (e: unknown) {
    if (typeof e === "object" && e !== null) {
      return { ok: false, status: e as RpcStatus };
    }
    throw e;
  }
}

/** kubectl runs a kubectl command and returns trimmed stdout. */
export function kubectl(...args: string[]): string {
  return execSync(`kubectl ${args.map((a) => `'${a.replace(/'/g, "'\\''")}'`).join(" ")}`, {
    encoding: "utf8",
  }).trim();
}

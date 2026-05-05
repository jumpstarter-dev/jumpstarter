// Shared runtime helpers used by every per-resource client wrapper.
//
// Two responsibilities only:
//
//   1. createClient(): a thin openapi-fetch wrapper that injects the bearer
//      token (from a static string OR a dynamic getter) on every request.
//
//   2. watchStream(): adapts a Watch* RPC's NDJSON HTTP response into an
//      AsyncIterable<T> so consumers can `for await (const ev of stream)`
//      without dealing with TextDecoder + line-splitting buffering.
//
// Everything else (typed paths, request/response shapes) flows from
// openapi-fetch + the generated schema modules.

import createOpenAPIClient, { type Client, type ClientOptions } from "openapi-fetch";

export type BearerSource = string | (() => string | Promise<string>);

export interface JumpstarterClientOptions {
  /** Base URL of the controller, e.g. "https://controller.example.com:8082". */
  baseUrl: string;
  /** Bearer token. Either a static value or a function that returns one. */
  bearer: BearerSource;
  /** Pass-through to the underlying fetch (e.g. for AbortController). */
  fetch?: typeof fetch;
}

export function createClient<Paths extends {}>(opts: JumpstarterClientOptions): Client<Paths> {
  const fetcher: typeof fetch = opts.fetch ?? fetch;
  const co: ClientOptions = {
    baseUrl: opts.baseUrl,
    fetch: fetcher,
  };
  const client = createOpenAPIClient<Paths>(co);
  client.use({
    async onRequest({ request }) {
      const token = typeof opts.bearer === "function" ? await opts.bearer() : opts.bearer;
      request.headers.set("authorization", `Bearer ${token}`);
      return request;
    },
  });
  return client;
}

/**
 * watchStream POSTs/GETs the given URL and yields one parsed JSON object
 * per `\n`-delimited line of the response body. Works in browsers (fetch
 * Streams + TextDecoderStream) and Node 20+ (same primitives).
 *
 * Bookmarks (event_type = EVENT_TYPE_BOOKMARK) are surfaced verbatim so
 * callers can persist resource_version checkpoints across reconnects.
 */
export async function* watchStream<T>(
  url: string,
  init: RequestInit & { bearer: BearerSource },
): AsyncIterable<T> {
  const token = typeof init.bearer === "function" ? await init.bearer() : init.bearer;
  const headers = new Headers(init.headers);
  headers.set("authorization", `Bearer ${token}`);
  headers.set("accept", "application/x-ndjson");
  const resp = await fetch(url, { ...init, headers });
  if (!resp.ok || !resp.body) {
    throw new Error(`watch ${url}: HTTP ${resp.status}`);
  }
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let nl: number;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      yield JSON.parse(line) as T;
    }
  }
}

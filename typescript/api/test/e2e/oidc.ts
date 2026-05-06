// Dex token helper for the TypeScript e2e suite.
//
// Mirrors `e2e/test/oidc_helper.go`: fetch the OIDC discovery doc,
// run a password grant against Dex's `local` connector, return the
// id_token. Dex's local connector keys on email, so the username
// "dev-alice" is logged in as "dev-alice@example.com".
//
// Tokens are cached in-process for ~30 minutes so repeated specs in
// the same suite don't pay the round-trip on every test.

const DEX_ISSUER = "https://dex.dex.svc.cluster.local:5556";
const DEX_CLIENT_ID = "jumpstarter-cli";
const DEX_AUDIENCE = "jumpstarter-cli";

export const DEX_TEST_PASSWORD = "password";

interface DexDiscovery {
  issuer: string;
  token_endpoint: string;
}

interface DexTokenResponse {
  id_token: string;
  access_token: string;
  expires_in: number;
}

interface CachedToken {
  idToken: string;
  expiresAt: number;
}

const cache = new Map<string, CachedToken>();
let discoveryPromise: Promise<DexDiscovery> | null = null;

async function fetchDiscovery(): Promise<DexDiscovery> {
  if (!discoveryPromise) {
    discoveryPromise = fetch(`${DEX_ISSUER}/.well-known/openid-configuration`).then(async (r) => {
      if (!r.ok) {
        throw new Error(`dex discovery returned ${r.status}: ${await r.text()}`);
      }
      const body = (await r.json()) as DexDiscovery;
      if (!body.token_endpoint) {
        throw new Error("dex discovery missing token_endpoint");
      }
      return body;
    });
  }
  return discoveryPromise;
}

/**
 * dexToken returns a Dex-issued id_token for the given username. The
 * caller passes the short form (e.g. "dev-alice") and the helper
 * appends "@example.com" because Dex's local connector keys on email.
 *
 * The controller validates these tokens via its multi-issuer JWT
 * authenticator and stamps the user as `dex:<username>` per the
 * claimMappings.username.prefix in e2e/values.kind.yaml.
 */
export async function dexToken(username: string, password = DEX_TEST_PASSWORD): Promise<string> {
  const cached = cache.get(username);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.idToken;
  }

  const disc = await fetchDiscovery();

  const loginID = username.includes("@") ? username : `${username}@example.com`;

  const form = new URLSearchParams();
  form.set("grant_type", "password");
  form.set("client_id", DEX_CLIENT_ID);
  form.set("username", loginID);
  form.set("password", password);
  form.set("scope", `openid profile email audience:server:client_id:${DEX_AUDIENCE}`);

  const resp = await fetch(disc.token_endpoint, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(`dex token request failed (${resp.status}): ${text}`);
  }
  const body = JSON.parse(text) as DexTokenResponse;
  if (!body.id_token) {
    throw new Error(`dex token response missing id_token: ${text}`);
  }

  const ttlSec = body.expires_in > 0 ? body.expires_in : 3600;
  const cappedSec = Math.min(ttlSec, 1800);
  cache.set(username, {
    idToken: body.id_token,
    expiresAt: Date.now() + cappedSec * 1000,
  });

  return body.id_token;
}

/** decodeJwtClaims returns the unverified payload of a JWT. */
export function decodeJwtClaims(token: string): Record<string, unknown> {
  const parts = token.split(".");
  if (parts.length < 2) {
    throw new Error(`malformed jwt: ${parts.length} segments`);
  }
  const payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const padded = payload + "=".repeat((4 - (payload.length % 4)) % 4);
  return JSON.parse(Buffer.from(padded, "base64").toString("utf8")) as Record<string, unknown>;
}

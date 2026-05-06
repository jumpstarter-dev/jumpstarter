// Loaded once per test file before any tests run.
//
// The controller serves on :8082 with a self-signed certificate and Dex
// is signed by the e2e CA. Node's built-in fetch has no easy way to
// supply a CA bundle, so we mirror what the Go e2e suite does:
// disable TLS verification globally for this suite. Token validation
// against Dex is the real auth boundary.
process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";

// Vitest's default unhandled rejection behavior is fine, but the SDK
// throws raw rpcStatus error objects from openapi-fetch which don't
// have stack traces — surface them when uncaught so failures aren't
// silently swallowed.
process.on("unhandledRejection", (reason) => {
  // eslint-disable-next-line no-console
  console.error("unhandled rejection in TS e2e:", reason);
});

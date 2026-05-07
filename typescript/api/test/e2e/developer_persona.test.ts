// Developer persona e2e tests for the TypeScript SDK.
//
// Mirrors the Go suite at e2e/test/admin_http_developer_persona_test.go
// but drives the controller via the @jumpstarter/api admin services.
// Validates the JEP-0014 self-service shape from the consumer side
// (Backstage / OpenShift Console / standalone web UI).

import { execSync } from "node:child_process";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { AdminClientService, type AdminClient } from "../../src/admin/client.js";
import { AdminExporterService } from "../../src/admin/exporter.js";
import { AdminLeaseService } from "../../src/admin/lease.js";
import { AdminWebhookService } from "../../src/admin/webhook.js";

import {
  baseUrl,
  captureRpc,
  clientName,
  exporterName,
  FOREIGN_NAMESPACE,
  GRPC,
  leaseName,
  namespace,
  nsParent,
  uniqueId,
} from "./helpers.js";
import { decodeJwtClaims, dexToken } from "./oidc.js";

describe("Admin HTTP API: developer self-service persona (TypeScript SDK)", () => {
  let alice: {
    clients: AdminClientService;
    exporters: AdminExporterService;
    leases: AdminLeaseService;
    webhooks: AdminWebhookService;
  };
  let bob: {
    clients: AdminClientService;
    exporters: AdminExporterService;
    leases: AdminLeaseService;
  };
  let ns: string;
  const fixtures: Array<{ kind: "clients" | "exporters" | "leases"; ns: string; id: string }> = [];

  beforeAll(async () => {
    ns = namespace();
    const aliceTok = await dexToken("dev-alice");
    const bobTok = await dexToken("dev-bob");

    alice = {
      clients: new AdminClientService({ baseUrl: baseUrl(), bearer: aliceTok }),
      exporters: new AdminExporterService({ baseUrl: baseUrl(), bearer: aliceTok }),
      leases: new AdminLeaseService({ baseUrl: baseUrl(), bearer: aliceTok }),
      webhooks: new AdminWebhookService({ baseUrl: baseUrl(), bearer: aliceTok }),
    };
    bob = {
      clients: new AdminClientService({ baseUrl: baseUrl(), bearer: bobTok }),
      exporters: new AdminExporterService({ baseUrl: baseUrl(), bearer: bobTok }),
      leases: new AdminLeaseService({ baseUrl: baseUrl(), bearer: bobTok }),
    };
  });

  afterAll(async () => {
    // Use the admin client to clean up — alice may have created
    // resources bob now technically also "owns" (post-update).
    const adminTok = await dexToken("jumpstarter-admin");
    const cleanup = {
      clients: new AdminClientService({ baseUrl: baseUrl(), bearer: adminTok }),
      exporters: new AdminExporterService({ baseUrl: baseUrl(), bearer: adminTok }),
      leases: new AdminLeaseService({ baseUrl: baseUrl(), bearer: adminTok }),
    };
    for (const f of fixtures) {
      try {
        if (f.kind === "clients") await cleanup.clients.delete(clientName(f.ns, f.id));
        if (f.kind === "exporters") await cleanup.exporters.delete(exporterName(f.ns, f.id));
        if (f.kind === "leases") await cleanup.leases.delete(leaseName(f.ns, f.id));
      } catch {
        /* best-effort */
      }
    }
  });

  describe("self-service create", () => {
    it("dev-alice creates a Client and gets back an inline_token whose iss is the controller's internal issuer", async () => {
      const id = uniqueId("ts-alice-ci");
      const created = await alice.clients.create(nsParent(ns), {} as AdminClient, id);
      fixtures.push({ kind: "clients", ns, id });

      const token = (created as Record<string, unknown> | undefined)?.["token"] as string | undefined;
      expect(token).toBeTruthy();

      const claims = decodeJwtClaims(token!);
      expect(claims.iss).toBe("https://localhost:8085");
    });

    it("admin.v1-created Clients carry type=CLIENT_TYPE_TOKEN", async () => {
      const id = uniqueId("ts-alice-token-type");
      const created = await alice.clients.create(nsParent(ns), {} as AdminClient, id);
      fixtures.push({ kind: "clients", ns, id });

      // Bot Clients minted via admin.v1 hold a static bootstrap token.
      expect(created?.type).toBe("CLIENT_TYPE_TOKEN");

      // Get returns the same classification (sanity — type is derived
      // on every read, not just at Create time).
      const got = await alice.clients.get(clientName(ns, id));
      expect(got?.type).toBe("CLIENT_TYPE_TOKEN");
    });
  });

  describe("namespace boundary — every verb denied outside the developer's project", () => {
    const cases: Array<{ name: string; verb: () => Promise<unknown> }> = [
      // Defined inside a test below so they capture the live `alice`.
    ];

    it.each([
      ["GET clients", () => alice.clients.get(clientName(FOREIGN_NAMESPACE, "any"))],
      ["LIST clients", () => alice.clients.list(nsParent(FOREIGN_NAMESPACE))],
      ["CREATE clients", () => alice.clients.create(nsParent(FOREIGN_NAMESPACE), {} as AdminClient, uniqueId("forbidden"))],
      ["UPDATE clients", () => alice.clients.update({ name: clientName(FOREIGN_NAMESPACE, "any"), labels: { x: "y" } })],
      ["DELETE clients", () => alice.clients.delete(clientName(FOREIGN_NAMESPACE, "any"))],

      ["GET exporters", () => alice.exporters.get(exporterName(FOREIGN_NAMESPACE, "any"))],
      ["LIST exporters", () => alice.exporters.list(nsParent(FOREIGN_NAMESPACE))],
      ["CREATE exporters", () => alice.exporters.create(nsParent(FOREIGN_NAMESPACE), {}, uniqueId("forbidden"))],
      ["UPDATE exporters", () => alice.exporters.update({ name: exporterName(FOREIGN_NAMESPACE, "any"), labels: { x: "y" } })],
      ["DELETE exporters", () => alice.exporters.delete(exporterName(FOREIGN_NAMESPACE, "any"))],

      ["GET leases", () => alice.leases.get(leaseName(FOREIGN_NAMESPACE, "any"))],
      ["LIST leases", () => alice.leases.list(nsParent(FOREIGN_NAMESPACE))],
      ["CREATE leases", () => alice.leases.create(nsParent(FOREIGN_NAMESPACE), {}, uniqueId("forbidden"))],
      ["UPDATE leases", () => alice.leases.update({ name: leaseName(FOREIGN_NAMESPACE, "any"), labels: { x: "y" } })],
      ["DELETE leases", () => alice.leases.delete(leaseName(FOREIGN_NAMESPACE, "any"))],
    ] as const)(
      "%s in foreign namespace returns PERMISSION_DENIED",
      async (_name: string, fn: () => Promise<unknown>) => {
        const r = await captureRpc(fn);
        expect(r.ok).toBe(false);
        if (!r.ok) expect(r.status.code).toBe(GRPC.PERMISSION_DENIED);
      },
    );

    // captures the unused declaration to keep TS happy
    void cases;

    it("WATCH in foreign namespace fails fast", async () => {
      const stream = alice.clients.watch(nsParent(FOREIGN_NAMESPACE));
      // The runtime throws on initial 4xx before yielding any event,
      // so iterating the AsyncIterable surfaces the error.
      const r = await captureRpc(async () => {
        for await (const _ev of stream) {
          // shouldn't yield any
          void _ev;
          break;
        }
      });
      expect(r.ok).toBe(false);
    });

    it("dev-alice cannot touch webhooks even in their own namespace", async () => {
      const r = await captureRpc(() =>
        alice.webhooks.create(nsParent(ns), { url: "https://example.invalid/h", secretRef: "wh-secret/key", events: ["EVENT_CLASS_LEASE_CREATED"] }, uniqueId("ts-alice-wh")),
      );
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.status.code).toBe(GRPC.PERMISSION_DENIED);

      const list = await captureRpc(() => alice.webhooks.list(nsParent(ns)));
      expect(list.ok).toBe(false);
      if (!list.ok) expect(list.status.code).toBe(GRPC.PERMISSION_DENIED);
    });
  });

  describe("ownership boundary between developers", () => {
    it("dev-bob cannot Update or Delete a Client owned by dev-alice (but can Get/List it)", async () => {
      const id = uniqueId("ts-alice-owns-client");
      await alice.clients.create(nsParent(ns), {} as AdminClient, id);
      fixtures.push({ kind: "clients", ns, id });

      // bob can read.
      const got = await bob.clients.get(clientName(ns, id));
      expect(got?.name).toBe(clientName(ns, id));

      // bob's update is rejected with owner-mismatch.
      const upd = await captureRpc(() =>
        bob.clients.update({ name: clientName(ns, id), labels: { hijacked: "yes" } }),
      );
      expect(upd.ok).toBe(false);
      if (!upd.ok) {
        expect(upd.status.code).toBe(GRPC.PERMISSION_DENIED);
        expect((upd.status.message ?? "").toLowerCase()).toContain("owner");
      }

      // bob's delete is rejected with owner-mismatch.
      const del = await captureRpc(() => bob.clients.delete(clientName(ns, id)));
      expect(del.ok).toBe(false);
      if (!del.ok) {
        expect(del.status.code).toBe(GRPC.PERMISSION_DENIED);
        expect((del.status.message ?? "").toLowerCase()).toContain("owner");
      }
    });

    it("dev-bob cannot Update or Delete an Exporter owned by dev-alice", async () => {
      const id = uniqueId("ts-alice-owns-exp");
      await alice.exporters.create(nsParent(ns), {}, id);
      fixtures.push({ kind: "exporters", ns, id });

      const upd = await captureRpc(() =>
        bob.exporters.update({ name: exporterName(ns, id), labels: { hijacked: "yes" } }),
      );
      expect(upd.ok).toBe(false);
      if (!upd.ok) expect(upd.status.code).toBe(GRPC.PERMISSION_DENIED);

      const del = await captureRpc(() => bob.exporters.delete(exporterName(ns, id)));
      expect(del.ok).toBe(false);
      if (!del.ok) expect(del.status.code).toBe(GRPC.PERMISSION_DENIED);
    });

    it("dev-alice can update and delete their own resources", async () => {
      const id = uniqueId("ts-alice-self-mut");
      await alice.clients.create(nsParent(ns), {} as AdminClient, id);

      const updated = await alice.clients.update({
        name: clientName(ns, id),
        labels: { "updated-by": "alice" },
      });
      expect(updated?.labels).toMatchObject({ "updated-by": "alice" });

      await alice.clients.delete(clientName(ns, id));
    });
  });

  describe("Client type classification (OIDC vs TOKEN)", () => {
    // Seeds a Client whose shape mirrors the auto-provisioned identity
    // Client the legacy client.v1 reconciler creates on first OIDC
    // contact: spec.username set, no admin owner annotation.
    const oidcShapedID = "ts-oidc-shaped";

    beforeAll(() => {
      // Apply via kubectl to bypass the admin Create handler (which
      // would unconditionally stamp the owner annotation and put it
      // into the SERVICE bucket).
      const manifest = `apiVersion: jumpstarter.dev/v1alpha1
kind: Client
metadata:
  name: ${oidcShapedID}
  namespace: ${ns}
spec:
  username: dex:dev-alice
`;
      execSync(`kubectl apply -f -`, { input: manifest, encoding: "utf8" });
      fixtures.push({ kind: "clients", ns, id: oidcShapedID });
    });

    it("an auto-provisioned-shaped Client is classified as CLIENT_TYPE_OIDC", async () => {
      const got = await alice.clients.get(clientName(ns, oidcShapedID));
      expect(got?.type).toBe("CLIENT_TYPE_OIDC");
    });
  });

  describe("lease lifecycle", () => {
    it("dev-alice creates → watches → releases a lease end-to-end", async () => {
      // alice needs a Client CRD owned by her in the namespace so
      // LeaseService.findClientForCaller can resolve the ClientRef.
      const ciId = uniqueId("ts-life-client");
      await alice.clients.create(nsParent(ns), {} as AdminClient, ciId);
      fixtures.push({ kind: "clients", ns, id: ciId });

      const expId = uniqueId("ts-life-exp");
      await alice.exporters.create(nsParent(ns), {}, expId);
      fixtures.push({ kind: "exporters", ns, id: expId });

      const leaseID = uniqueId("ts-life-lease");
      const targetLeaseName = leaseName(ns, leaseID);

      const stream = alice.leases.watch(nsParent(ns));
      const seen = new Promise<string>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("lease watch timed out")), 30_000);
        (async () => {
          try {
            for await (const ev of stream) {
              if (ev.lease?.name === targetLeaseName) {
                clearTimeout(timer);
                resolve(ev.eventType);
                return;
              }
            }
            reject(new Error("watch ended before event"));
          } catch (e) {
            reject(e);
          }
        })();
      });

      await new Promise((r) => setTimeout(r, 200));

      await alice.leases.create(
        nsParent(ns),
        { exporterName: exporterName(ns, expId), duration: "60s" },
        leaseID,
      );
      fixtures.push({ kind: "leases", ns, id: leaseID });

      const eventType = await seen;
      expect(eventType).toMatch(/EVENT_TYPE_(ADDED|MODIFIED)/);

      // DeleteLease is the soft-release path (sets Spec.Release=true).
      await alice.leases.delete(targetLeaseName);
    }, 60_000);
  });
});

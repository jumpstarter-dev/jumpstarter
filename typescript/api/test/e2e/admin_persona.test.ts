// Admin persona e2e tests for the TypeScript SDK.
//
// Mirrors the Go suite at e2e/test/admin_http_admin_persona_test.go but
// drives the controller via the @jumpstarter/api admin services rather
// than raw fetch. Validates that a web-portal-style consumer can speak
// the same wire format with full type safety.

import { execSync } from "node:child_process";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { AdminClientService, type AdminClient } from "../../src/admin/client.js";
import { AdminExporterService } from "../../src/admin/exporter.js";
import { AdminLeaseService } from "../../src/admin/lease.js";

import {
  baseUrl,
  captureRpc,
  clientName,
  exporterName,
  GRPC,
  namespace,
  nsParent,
  uniqueId,
} from "./helpers.js";
import { dexToken } from "./oidc.js";

describe("Admin HTTP API: full admin persona (TypeScript SDK)", () => {
  let admin: {
    clients: AdminClientService;
    exporters: AdminExporterService;
    leases: AdminLeaseService;
  };
  let alice: {
    clients: AdminClientService;
    exporters: AdminExporterService;
  };
  let ns: string;
  const fixtures: Array<{ kind: "clients" | "exporters" | "leases"; ns: string; id: string }> = [];

  beforeAll(async () => {
    ns = namespace();

    const adminTok = await dexToken("jumpstarter-admin");
    const aliceTok = await dexToken("dev-alice");

    admin = {
      clients: new AdminClientService({ baseUrl: baseUrl(), bearer: adminTok }),
      exporters: new AdminExporterService({ baseUrl: baseUrl(), bearer: adminTok }),
      leases: new AdminLeaseService({ baseUrl: baseUrl(), bearer: adminTok }),
    };
    alice = {
      clients: new AdminClientService({ baseUrl: baseUrl(), bearer: aliceTok }),
      exporters: new AdminExporterService({ baseUrl: baseUrl(), bearer: aliceTok }),
    };
  });

  afterAll(async () => {
    for (const f of fixtures) {
      try {
        if (f.kind === "clients") {
          await admin.clients.delete(clientName(f.ns, f.id));
        } else if (f.kind === "exporters") {
          await admin.exporters.delete(exporterName(f.ns, f.id));
        }
      } catch {
        /* best-effort */
      }
    }
  });

  it("lists clients in the operator-watched namespace", async () => {
    const id = uniqueId("ts-admin-list");
    const created = await admin.clients.create(nsParent(ns), {} as AdminClient, id);
    expect(created?.name).toBe(clientName(ns, id));
    fixtures.push({ kind: "clients", ns, id });

    const list = await admin.clients.list(nsParent(ns));
    const names = (list?.clients ?? []).map((c) => c.name);
    expect(names).toContain(clientName(ns, id));
  });

  it("creates a Client and receives an inline bootstrap token", async () => {
    const id = uniqueId("ts-admin-bootstrap");
    const created = await admin.clients.create(nsParent(ns), {} as AdminClient, id);
    fixtures.push({ kind: "clients", ns, id });

    // The proto field is `token` (snake) → `token` (camel preserved by
    // openapi-typescript). Some controller versions also exposed an
    // `inline_token` alias; accept either.
    const token = (created as Record<string, unknown> | undefined)?.["token"] as string | undefined;
    expect(token).toBeTruthy();
  });

  it("admin can update and delete a Client owned by dev-alice", async () => {
    const id = uniqueId("ts-alice-owned");

    // dev-alice creates the resource.
    await alice.clients.create(nsParent(ns), {} as AdminClient, id);

    // admin updates with new labels.
    const updated = await admin.clients.update({
      name: clientName(ns, id),
      labels: { env: "ts-admin-touched" },
    });
    expect(updated?.labels).toMatchObject({ env: "ts-admin-touched" });

    // admin deletes.
    await admin.clients.delete(clientName(ns, id));

    // confirm 404 by attempting Get.
    const got = await captureRpc(() => admin.clients.get(clientName(ns, id)));
    expect(got.ok).toBe(false);
    if (!got.ok) expect(got.status.code).toBe(GRPC.NOT_FOUND);
  });

  it("admin can update an Exporter owned by dev-alice", async () => {
    const id = uniqueId("ts-alice-exp");
    await alice.exporters.create(nsParent(ns), {}, id);
    fixtures.push({ kind: "exporters", ns, id });

    const updated = await admin.exporters.update({
      name: exporterName(ns, id),
      labels: { env: "ts-admin-touched" },
    });
    expect(updated?.labels).toMatchObject({ env: "ts-admin-touched" });
  });

  it("admin's cluster-wide watch sees a developer's Client mutation", async () => {
    const watchID = uniqueId("ts-watch-alice");
    const targetName = clientName(ns, watchID);

    const stream = admin.clients.watch(nsParent(ns));
    const seen = new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("watch timed out")), 30_000);
      (async () => {
        try {
          for await (const ev of stream) {
            if (ev.client?.name === targetName) {
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

    // Tiny delay to let the watch open before we mutate.
    await new Promise((r) => setTimeout(r, 200));
    await alice.clients.create(nsParent(ns), {} as AdminClient, watchID);
    fixtures.push({ kind: "clients", ns, id: watchID });

    const eventType = await seen;
    expect(eventType).toMatch(/EVENT_TYPE_(ADDED|MODIFIED)/);
  }, 60_000);

  describe("externally-managed resources are read-only via admin.v1", () => {
    // GitOps-tracked resources should not be mutated through the
    // admin API — any change would be reverted on the next
    // reconciliation. The controller refuses Update/Delete on any
    // resource carrying app.kubernetes.io/managed-by; the SDK
    // surfaces the signal as `metadata.externallyManaged`.

    const argoClientID = "ts-argo-client";
    const argoExpID = "ts-argo-exp";

    beforeAll(() => {
      execSync(
        "kubectl apply -f -",
        {
          input: `apiVersion: jumpstarter.dev/v1alpha1
kind: Client
metadata:
  name: ${argoClientID}
  namespace: ${ns}
  labels:
    app.kubernetes.io/managed-by: argocd
---
apiVersion: jumpstarter.dev/v1alpha1
kind: Exporter
metadata:
  name: ${argoExpID}
  namespace: ${ns}
  labels:
    app.kubernetes.io/managed-by: argocd
`,
          encoding: "utf8",
        },
      );
    });

    afterAll(() => {
      execSync(
        `kubectl delete client ${argoClientID} -n ${ns} --ignore-not-found && ` +
          `kubectl delete exporter ${argoExpID} -n ${ns} --ignore-not-found`,
        { encoding: "utf8" },
      );
    });

    it("Get returns externallyManaged=true", async () => {
      const c = await admin.clients.get(clientName(ns, argoClientID));
      expect(c?.metadata?.externallyManaged).toBe(true);

      const e = await admin.exporters.get(exporterName(ns, argoExpID));
      expect(e?.metadata?.externallyManaged).toBe(true);
    });

    it("Update on Client is refused with FailedPrecondition", async () => {
      const r = await captureRpc(() =>
        admin.clients.update({
          name: clientName(ns, argoClientID),
          labels: { x: "y" },
        }),
      );
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.status.code).toBe(GRPC.FAILED_PRECONDITION);
    });

    it("Delete on Client is refused with FailedPrecondition", async () => {
      const r = await captureRpc(() => admin.clients.delete(clientName(ns, argoClientID)));
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.status.code).toBe(GRPC.FAILED_PRECONDITION);
    });

    it("Update + Delete on Exporter are refused", async () => {
      const upd = await captureRpc(() =>
        admin.exporters.update({
          name: exporterName(ns, argoExpID),
          labels: { x: "y" },
        }),
      );
      expect(upd.ok).toBe(false);
      if (!upd.ok) expect(upd.status.code).toBe(GRPC.FAILED_PRECONDITION);

      const del = await captureRpc(() => admin.exporters.delete(exporterName(ns, argoExpID)));
      expect(del.ok).toBe(false);
      if (!del.ok) expect(del.status.code).toBe(GRPC.FAILED_PRECONDITION);
    });
  });
});

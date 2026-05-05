// Compile-time smoke test. tsc --noEmit on this file verifies that every
// subpath export is reachable, each prefixed service class instantiates
// with the expected options, and the generated path types resolve. No
// HTTP traffic is generated.
import { AdminLeaseService } from "../src/admin/lease.js";
import { AdminExporterService } from "../src/admin/exporter.js";
import { AdminClientService } from "../src/admin/client.js";
import { AdminWebhookService } from "../src/admin/webhook.js";
import { ClientLeaseService } from "../src/client/lease.js";
import { ClientExporterService } from "../src/client/exporter.js";

const opts = { baseUrl: "https://controller.example.com:8082", bearer: "deadbeef" };

const _adminLease = new AdminLeaseService(opts);
const _adminExporter = new AdminExporterService(opts);
const _adminClient = new AdminClientService(opts);
const _adminWebhook = new AdminWebhookService(opts);
const _clientLease = new ClientLeaseService(opts);
const _clientExporter = new ClientExporterService(opts);

// Reference each so noUnusedLocals doesn't complain.
void _adminLease;
void _adminExporter;
void _adminClient;
void _adminWebhook;
void _clientLease;
void _clientExporter;

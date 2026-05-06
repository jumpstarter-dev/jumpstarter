// @jumpstarter/api/admin/client — typed wrapper around
// jumpstarter.admin.v1.ClientService (CRUD over the Client kube resource).
//
// Note the deliberate name overlap with @jumpstarter/api/client/* (those
// modules expose the namespace-scoped Client-actor API in client.v1). The
// import path tells the two apart at every call site, and Admin* /
// Client* prefixes keep auto-import suggestions disambiguated.
import type { paths } from "../_generated/admin/client.js";
import { createClient, type JumpstarterClientOptions, watchStream } from "../_runtime.js";

export type AdminClient = NonNullable<paths["/admin/v1/{name}"]["get"]["responses"]["200"]["content"]["application/json"]>;
// gRPC-gateway emits camelCase JSON for streaming responses (and
// non-streaming, for that matter). The field shapes mirror the proto
// LeaseEvent / ClientEvent / etc. messages.
export type AdminClientEvent = { eventType: string; client?: AdminClient; resourceVersion: string };

export class AdminClientService {
  private readonly api: ReturnType<typeof createClient<paths>>;
  private readonly opts: JumpstarterClientOptions;
  constructor(opts: JumpstarterClientOptions) {
    this.api = createClient<paths>(opts);
    this.opts = opts;
  }

  async get(name: string) {
    const { data, error } = await this.api.GET("/admin/v1/{name}", { params: { path: { name } } });
    if (error) throw error;
    return data;
  }

  async list(parent: string, params?: { pageSize?: number; pageToken?: string; filter?: string }) {
    const { data, error } = await this.api.GET("/admin/v1/{parent}/clients", {
      params: { path: { parent }, query: params ?? {} },
    });
    if (error) throw error;
    return data;
  }

  /**
   * create writes a new Client CRD and waits for the controller to mint a
   * bootstrap credential. The returned Client carries `bootstrap_token`
   * inline.
   */
  async create(parent: string, client: AdminClient, clientId?: string) {
    const { data, error } = await this.api.POST("/admin/v1/{parent}/clients", {
      params: { path: { parent }, query: clientId ? { clientId } : {} },
      body: client,
    });
    if (error) throw error;
    return data;
  }

  async update(client: AdminClient & { name: string }) {
    const { data, error } = await this.api.PATCH("/admin/v1/{client.name}", {
      params: {
        path: { "client.name": client.name },
      },
      body: client,
    });
    if (error) throw error;
    return data;
  }

  async delete(name: string) {
    const { error } = await this.api.DELETE("/admin/v1/{name}", { params: { path: { name } } });
    if (error) throw error;
  }

  watch(parent: string, opts?: { resourceVersion?: string; filter?: string }) {
    const url = new URL(`${this.opts.baseUrl}/admin/v1/${parent}/clients:watch`);
    if (opts?.resourceVersion) url.searchParams.set("resource_version", opts.resourceVersion);
    if (opts?.filter) url.searchParams.set("filter", opts.filter);
    return watchStream<AdminClientEvent>(url.toString(), { bearer: this.opts.bearer, method: "GET" });
  }
}

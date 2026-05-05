// @jumpstarter/api/admin/lease — typed wrapper around
// jumpstarter.admin.v1.LeaseService.
//
// Type / class names are prefixed with Admin* so TypeScript auto-import
// can disambiguate them from the namespace-scoped client.v1 wrappers
// (ClientLease/ClientLeaseService).
import type { paths } from "../_generated/admin/lease.js";
import { createClient, type JumpstarterClientOptions, watchStream } from "../_runtime.js";

export type AdminLease = NonNullable<paths["/admin/v1/{name}"]["get"]["responses"]["200"]["content"]["application/json"]>;
export type AdminLeaseEvent = { event_type: string; lease?: AdminLease; resource_version: string };

export class AdminLeaseService {
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

  async list(parent: string, params?: { pageSize?: number; pageToken?: string; filter?: string; onlyActive?: boolean; tagFilter?: string }) {
    const { data, error } = await this.api.GET("/admin/v1/{parent}/leases", {
      params: { path: { parent }, query: params ?? {} },
    });
    if (error) throw error;
    return data;
  }

  /**
   * create issues a CreateLease against /admin/v1/{parent}/leases. The
   * server stamps the caller's owner annotations and returns the
   * persisted resource.
   */
  async create(parent: string, lease: AdminLease, leaseId?: string) {
    const { data, error } = await this.api.POST("/admin/v1/{parent}/leases", {
      params: { path: { parent }, query: leaseId ? { leaseId } : {} },
      body: lease,
    });
    if (error) throw error;
    return data;
  }

  /**
   * update patches mutable fields on an existing Lease. The lease.name
   * field on the input identifies the target resource; if updateMask is
   * provided, only listed paths are applied.
   */
  async update(lease: AdminLease & { name: string }) {
    const { data, error } = await this.api.PATCH("/admin/v1/{lease.name}", {
      params: {
        path: { "lease.name": lease.name },
      },
      body: lease,
    });
    if (error) throw error;
    return data;
  }

  async delete(name: string) {
    const { error } = await this.api.DELETE("/admin/v1/{name}", { params: { path: { name } } });
    if (error) throw error;
  }

  /**
   * watch returns an AsyncIterable that yields event envelopes until the
   * caller breaks the loop or the server closes the stream. Resume by
   * passing the last seen resource_version.
   */
  watch(parent: string, opts?: { resourceVersion?: string; filter?: string }) {
    const url = new URL(`${this.opts.baseUrl}/admin/v1/${parent}/leases:watch`);
    if (opts?.resourceVersion) url.searchParams.set("resource_version", opts.resourceVersion);
    if (opts?.filter) url.searchParams.set("filter", opts.filter);
    return watchStream<AdminLeaseEvent>(url.toString(), { bearer: this.opts.bearer, method: "GET" });
  }
}

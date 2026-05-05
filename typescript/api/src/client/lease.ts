// @jumpstarter/api/client/lease — typed wrapper around the legacy
// jumpstarter.client.v1.ClientService Lease* RPCs.
//
// This is the surface a browser-based "thin client" uses to act AS a
// Client actor (using the actor's auto-provisioned object-token), in
// contrast to @jumpstarter/api/admin/* which administers the Client
// resource itself.
import type { paths } from "../_generated/client/client.js";
import { createClient, type JumpstarterClientOptions } from "../_runtime.js";

export type ClientLease = NonNullable<paths["/client/v1/{name}"]["get"]["responses"]["200"]["content"]["application/json"]>;

export class ClientLeaseService {
  private readonly api: ReturnType<typeof createClient<paths>>;
  constructor(opts: JumpstarterClientOptions) {
    this.api = createClient<paths>(opts);
  }

  async get(name: string) {
    const { data, error } = await this.api.GET("/client/v1/{name}", { params: { path: { name } } });
    if (error) throw error;
    return data;
  }

  async list(parent: string, params?: { pageSize?: number; pageToken?: string; filter?: string; onlyActive?: boolean; tagFilter?: string }) {
    const { data, error } = await this.api.GET("/client/v1/{parent}/leases", {
      params: { path: { parent }, query: params ?? {} },
    });
    if (error) throw error;
    return data;
  }

  async create(parent: string, lease: ClientLease, leaseId?: string) {
    const { data, error } = await this.api.POST("/client/v1/{parent}/leases", {
      params: { path: { parent }, query: leaseId ? { leaseId } : {} },
      body: lease,
    });
    if (error) throw error;
    return data;
  }

  async update(lease: ClientLease & { name: string }) {
    const { data, error } = await this.api.PATCH("/client/v1/{lease.name}", {
      params: {
        path: { "lease.name": lease.name },
      },
      body: lease,
    });
    if (error) throw error;
    return data;
  }

  async delete(name: string) {
    const { error } = await this.api.DELETE("/client/v1/{name}", { params: { path: { name } } });
    if (error) throw error;
  }
}

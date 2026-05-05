// @jumpstarter/api/client/exporter — typed wrapper around the legacy
// jumpstarter.client.v1.ClientService Exporter* RPCs.
import type { paths } from "../_generated/client/client.js";
import { createClient, type JumpstarterClientOptions } from "../_runtime.js";

export type ClientExporter = NonNullable<paths["/client/v1/{name}"]["get"]["responses"]["200"]["content"]["application/json"]>;

export class ClientExporterService {
  private readonly api: ReturnType<typeof createClient<paths>>;
  constructor(opts: JumpstarterClientOptions) {
    this.api = createClient<paths>(opts);
  }

  async get(name: string) {
    const { data, error } = await this.api.GET("/client/v1/{name}", { params: { path: { name } } });
    if (error) throw error;
    return data;
  }

  async list(parent: string, params?: { pageSize?: number; pageToken?: string; filter?: string }) {
    const { data, error } = await this.api.GET("/client/v1/{parent}/exporters", {
      params: { path: { parent }, query: params ?? {} },
    });
    if (error) throw error;
    return data;
  }
}

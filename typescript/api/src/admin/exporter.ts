// @jumpstarter/api/admin/exporter — typed wrapper around
// jumpstarter.admin.v1.ExporterService.
import type { paths } from "../_generated/admin/exporter.js";
import { createClient, type JumpstarterClientOptions, watchStream } from "../_runtime.js";

export type AdminExporter = NonNullable<paths["/admin/v1/{name}"]["get"]["responses"]["200"]["content"]["application/json"]>;
export type AdminExporterEvent = { event_type: string; exporter?: AdminExporter; resource_version: string };

export class AdminExporterService {
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
    const { data, error } = await this.api.GET("/admin/v1/{parent}/exporters", {
      params: { path: { parent }, query: params ?? {} },
    });
    if (error) throw error;
    return data;
  }

  /**
   * create writes a new Exporter and waits for the controller to mint a
   * bootstrap credential. The returned Exporter carries `bootstrap_token`
   * inline, so the caller never polls for a Secret to appear.
   */
  async create(parent: string, exporter: AdminExporter, exporterId?: string) {
    const { data, error } = await this.api.POST("/admin/v1/{parent}/exporters", {
      params: { path: { parent }, query: exporterId ? { exporterId } : {} },
      body: exporter,
    });
    if (error) throw error;
    return data;
  }

  async update(exporter: AdminExporter & { name: string }) {
    const { data, error } = await this.api.PATCH("/admin/v1/{exporter.name}", {
      params: {
        path: { "exporter.name": exporter.name },
      },
      body: exporter,
    });
    if (error) throw error;
    return data;
  }

  async delete(name: string) {
    const { error } = await this.api.DELETE("/admin/v1/{name}", { params: { path: { name } } });
    if (error) throw error;
  }

  watch(parent: string, opts?: { resourceVersion?: string; filter?: string }) {
    const url = new URL(`${this.opts.baseUrl}/admin/v1/${parent}/exporters:watch`);
    if (opts?.resourceVersion) url.searchParams.set("resource_version", opts.resourceVersion);
    if (opts?.filter) url.searchParams.set("filter", opts.filter);
    return watchStream<AdminExporterEvent>(url.toString(), { bearer: this.opts.bearer, method: "GET" });
  }
}

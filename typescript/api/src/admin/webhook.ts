// @jumpstarter/api/admin/webhook — typed wrapper around
// jumpstarter.admin.v1.WebhookService.
import type { paths } from "../_generated/admin/webhook.js";
import { createClient, type JumpstarterClientOptions } from "../_runtime.js";

export type AdminWebhook = NonNullable<paths["/admin/v1/{name}"]["get"]["responses"]["200"]["content"]["application/json"]>;

export class AdminWebhookService {
  private readonly api: ReturnType<typeof createClient<paths>>;
  constructor(opts: JumpstarterClientOptions) {
    this.api = createClient<paths>(opts);
  }

  async get(name: string) {
    const { data, error } = await this.api.GET("/admin/v1/{name}", { params: { path: { name } } });
    if (error) throw error;
    return data;
  }

  async list(parent: string, params?: { pageSize?: number; pageToken?: string }) {
    const { data, error } = await this.api.GET("/admin/v1/{parent}/webhooks", {
      params: { path: { parent }, query: params ?? {} },
    });
    if (error) throw error;
    return data;
  }

  async create(parent: string, webhook: AdminWebhook, webhookId?: string) {
    const { data, error } = await this.api.POST("/admin/v1/{parent}/webhooks", {
      params: { path: { parent }, query: webhookId ? { webhookId } : {} },
      body: webhook,
    });
    if (error) throw error;
    return data;
  }

  async update(webhook: AdminWebhook & { name: string }) {
    const { data, error } = await this.api.PATCH("/admin/v1/{webhook.name}", {
      params: {
        path: { "webhook.name": webhook.name },
      },
      body: webhook,
    });
    if (error) throw error;
    return data;
  }

  async delete(name: string) {
    const { error } = await this.api.DELETE("/admin/v1/{name}", { params: { path: { name } } });
    if (error) throw error;
  }
}

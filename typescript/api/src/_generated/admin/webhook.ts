// generated from admin/v1/webhook.swagger.json — do not edit by hand
export interface paths {
    "/admin/v1/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["WebhookService_GetWebhook"];
        put?: never;
        post?: never;
        delete: operations["WebhookService_DeleteWebhook"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/{parent}/webhooks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["WebhookService_ListWebhooks"];
        put?: never;
        post: operations["WebhookService_CreateWebhook"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/{webhook.name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch: operations["WebhookService_UpdateWebhook"];
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        protobufAny: {
            "@type"?: string;
        } & {
            [key: string]: unknown;
        };
        rpcStatus: {
            /** Format: int32 */
            code?: number;
            message?: string;
            details?: components["schemas"]["protobufAny"][];
        };
        /**
         * @description EventClass enumerates the webhook event categories a subscriber can
         *     register interest in. Mirrored to/from the Webhook CRD's spec.events
         *     list as strings.
         * @default EVENT_CLASS_UNSPECIFIED
         * @enum {string}
         */
        v1EventClass: "EVENT_CLASS_UNSPECIFIED" | "EVENT_CLASS_LEASE_CREATED" | "EVENT_CLASS_LEASE_ENDED" | "EVENT_CLASS_EXPORTER_OFFLINE" | "EVENT_CLASS_EXPORTER_AVAILABLE" | "EVENT_CLASS_CLIENT_CREATED" | "EVENT_CLASS_CLIENT_DELETED";
        /**
         * @description ResourceMetadata bundles the identity-attribution and concurrency-control
         *     fields the controller stamps onto every Jumpstarter resource. Each domain
         *     resource embeds this as a field named "metadata" so admin clients see a
         *     uniform shape across resources.
         */
        v1ResourceMetadata: {
            /**
             * @description Display-only username of the OIDC caller that last mutated this
             *     resource (mirrored from the jumpstarter.dev/created-by annotation).
             *     Optional because resources created by GitOps / kubectl may not carry it.
             */
            readonly createdBy?: string;
            /** @description OIDC issuer URL of the owner (jumpstarter.dev/owner-issuer annotation). */
            readonly ownerIssuer?: string;
            /**
             * @description Kubernetes resourceVersion. Used by clients for optimistic concurrency
             *     and by Watch* RPCs as a resume cursor.
             */
            readonly resourceVersion?: string;
            /**
             * @description True when the resource is tracked by an external tool (ArgoCD,
             *     Flux, Helm, kustomize-controller, …). The controller refuses
             *     mutations on these via admin.v1 because edits would be reverted
             *     on the next reconciliation; cluster admins can still kubectl-edit.
             *     Derived from labels/annotations on every read.
             */
            readonly externallyManaged?: boolean;
        };
        v1Webhook: {
            name?: string;
            /** @description Destination URL the controller POSTs signed event payloads to. */
            url: string;
            /**
             * @description Reference to a Secret in the same namespace whose value is used as the
             *     HMAC-SHA256 signing key. Format: "<secret-name>/<key>".
             */
            secretRef: string;
            events: components["schemas"]["v1EventClass"][];
            /** Format: date-time */
            readonly lastSuccess?: string;
            /** Format: date-time */
            readonly lastFailure?: string;
            /** Format: int32 */
            readonly consecutiveFailures?: number;
            metadata?: components["schemas"]["v1ResourceMetadata"];
        };
        v1WebhookListResponse: {
            webhooks?: components["schemas"]["v1Webhook"][];
            nextPageToken?: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    WebhookService_GetWebhook: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /**
                 * @description Resource name in the form "namespaces/{namespace}/{plural}/{id}",
                 *     e.g. "namespaces/lab-foo/leases/abcd1234".
                 */
                name: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description A successful response. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["v1Webhook"];
                };
            };
            /** @description An unexpected error response. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["rpcStatus"];
                };
            };
        };
    };
    WebhookService_DeleteWebhook: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description A successful response. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": Record<string, never>;
                };
            };
            /** @description An unexpected error response. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["rpcStatus"];
                };
            };
        };
    };
    WebhookService_ListWebhooks: {
        parameters: {
            query?: {
                pageSize?: number;
                pageToken?: string;
            };
            header?: never;
            path: {
                parent: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description A successful response. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["v1WebhookListResponse"];
                };
            };
            /** @description An unexpected error response. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["rpcStatus"];
                };
            };
        };
    };
    WebhookService_CreateWebhook: {
        parameters: {
            query?: {
                webhookId?: string;
            };
            header?: never;
            path: {
                parent: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["v1Webhook"];
            };
        };
        responses: {
            /** @description A successful response. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["v1Webhook"];
                };
            };
            /** @description An unexpected error response. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["rpcStatus"];
                };
            };
        };
    };
    WebhookService_UpdateWebhook: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                "webhook.name": string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    /** @description Destination URL the controller POSTs signed event payloads to. */
                    url: string;
                    /**
                     * @description Reference to a Secret in the same namespace whose value is used as the
                     *     HMAC-SHA256 signing key. Format: "<secret-name>/<key>".
                     */
                    secretRef: string;
                    events: components["schemas"]["v1EventClass"][];
                    /** Format: date-time */
                    readonly lastSuccess?: string;
                    /** Format: date-time */
                    readonly lastFailure?: string;
                    /** Format: int32 */
                    readonly consecutiveFailures?: number;
                    metadata?: components["schemas"]["v1ResourceMetadata"];
                };
            };
        };
        responses: {
            /** @description A successful response. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["v1Webhook"];
                };
            };
            /** @description An unexpected error response. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["rpcStatus"];
                };
            };
        };
    };
}

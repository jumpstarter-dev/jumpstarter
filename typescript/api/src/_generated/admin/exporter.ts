// generated from admin/v1/exporter.swagger.json — do not edit by hand
export interface paths {
    "/admin/v1/{exporter.name}": {
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
        patch: operations["ExporterService_UpdateExporter"];
        trace?: never;
    };
    "/admin/v1/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["ExporterService_GetExporter"];
        put?: never;
        post?: never;
        delete: operations["ExporterService_DeleteExporter"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/{parent}/exporters": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["ExporterService_ListExporters"];
        put?: never;
        post: operations["ExporterService_CreateExporter"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/{parent}/exporters:watch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["ExporterService_WatchExporters"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
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
         * @description EventType classifies a Watch* stream event. Mirrors Kubernetes
         *     watch.Event semantics (ADDED, MODIFIED, DELETED, BOOKMARK).
         *
         *      - EVENT_TYPE_BOOKMARK: BOOKMARK carries no resource payload, only a resource_version
         *     checkpoint. Servers emit bookmarks every ~30s on otherwise idle
         *     streams to keep HTTP connections alive across idle timeouts and
         *     to give clients a resumable cursor.
         * @default EVENT_TYPE_UNSPECIFIED
         * @enum {string}
         */
        v1EventType: "EVENT_TYPE_UNSPECIFIED" | "EVENT_TYPE_ADDED" | "EVENT_TYPE_MODIFIED" | "EVENT_TYPE_DELETED" | "EVENT_TYPE_BOOKMARK";
        /** @description Exporter is the canonical Jumpstarter Exporter resource shape. */
        v1Exporter: {
            name?: string;
            labels?: {
                [key: string]: string;
            };
            /**
             * @description Deprecated boolean liveness signal kept for client.v1 wire compatibility.
             *     Use status instead.
             */
            readonly online?: boolean;
            status?: components["schemas"]["v1ExporterStatus"];
            readonly statusMessage?: string;
            /**
             * @description Admin-surface extensions (>= 6 so legacy client.v1 callers don't
             *     collide with these new fields). All are optional because the legacy
             *     client.v1 surface never populates them and admin Get/List/Watch may
             *     also legitimately omit token after the bootstrap secret is rotated.
             */
            username?: string;
            readonly endpoint?: string;
            /**
             * @description token is populated only on CreateExporter responses (the bootstrap
             *     credential the controller mints inline) and is never echoed on
             *     Get/List/Update/Watch.
             */
            readonly token?: string;
            metadata?: components["schemas"]["v1ResourceMetadata"];
        };
        v1ExporterEvent: {
            eventType?: components["schemas"]["v1EventType"];
            exporter?: components["schemas"]["v1Exporter"];
            resourceVersion?: string;
        };
        v1ExporterListResponse: {
            exporters?: components["schemas"]["v1Exporter"][];
            nextPageToken?: string;
        };
        /**
         * Exporter status information
         * @description - EXPORTER_STATUS_UNSPECIFIED: Unspecified exporter status
         *      - EXPORTER_STATUS_OFFLINE: Exporter is offline
         *      - EXPORTER_STATUS_AVAILABLE: Exporter is available to be leased
         *      - EXPORTER_STATUS_BEFORE_LEASE_HOOK: Exporter is executing before lease hook(s)
         *      - EXPORTER_STATUS_LEASE_READY: Exporter is leased and ready to accept commands
         *      - EXPORTER_STATUS_AFTER_LEASE_HOOK: Exporter is executing after lease hook(s)
         *      - EXPORTER_STATUS_BEFORE_LEASE_HOOK_FAILED: Exporter before lease hook failed
         *      - EXPORTER_STATUS_AFTER_LEASE_HOOK_FAILED: Exporter after lease hook failed
         * @default EXPORTER_STATUS_UNSPECIFIED
         * @enum {string}
         */
        v1ExporterStatus: "EXPORTER_STATUS_UNSPECIFIED" | "EXPORTER_STATUS_OFFLINE" | "EXPORTER_STATUS_AVAILABLE" | "EXPORTER_STATUS_BEFORE_LEASE_HOOK" | "EXPORTER_STATUS_LEASE_READY" | "EXPORTER_STATUS_AFTER_LEASE_HOOK" | "EXPORTER_STATUS_BEFORE_LEASE_HOOK_FAILED" | "EXPORTER_STATUS_AFTER_LEASE_HOOK_FAILED";
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
    ExporterService_UpdateExporter: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                "exporter.name": string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    labels?: {
                        [key: string]: string;
                    };
                    /**
                     * @description Deprecated boolean liveness signal kept for client.v1 wire compatibility.
                     *     Use status instead.
                     */
                    readonly online?: boolean;
                    status?: components["schemas"]["v1ExporterStatus"];
                    readonly statusMessage?: string;
                    /**
                     * @description Admin-surface extensions (>= 6 so legacy client.v1 callers don't
                     *     collide with these new fields). All are optional because the legacy
                     *     client.v1 surface never populates them and admin Get/List/Watch may
                     *     also legitimately omit token after the bootstrap secret is rotated.
                     */
                    username?: string;
                    readonly endpoint?: string;
                    /**
                     * @description token is populated only on CreateExporter responses (the bootstrap
                     *     credential the controller mints inline) and is never echoed on
                     *     Get/List/Update/Watch.
                     */
                    readonly token?: string;
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
                    "application/json": components["schemas"]["v1Exporter"];
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
    ExporterService_GetExporter: {
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
                    "application/json": components["schemas"]["v1Exporter"];
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
    ExporterService_DeleteExporter: {
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
    ExporterService_ListExporters: {
        parameters: {
            query?: {
                pageSize?: number;
                pageToken?: string;
                filter?: string;
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
                    "application/json": components["schemas"]["v1ExporterListResponse"];
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
    ExporterService_CreateExporter: {
        parameters: {
            query?: {
                exporterId?: string;
            };
            header?: never;
            path: {
                parent: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["v1Exporter"];
            };
        };
        responses: {
            /** @description A successful response. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["v1Exporter"];
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
    ExporterService_WatchExporters: {
        parameters: {
            query?: {
                /**
                 * @description Resume marker. Empty string starts from the current state; otherwise
                 *     resumes after the given resourceVersion. The server returns
                 *     OUT_OF_RANGE if the version is too old to resume from informer cache.
                 */
                resourceVersion?: string;
                /** @description Optional Kubernetes label selector applied server-side. */
                filter?: string;
            };
            header?: never;
            path: {
                /** @description Parent collection name, e.g. "namespaces/lab-foo". */
                parent: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description A successful response.(streaming responses) */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        result?: components["schemas"]["v1ExporterEvent"];
                        error?: components["schemas"]["rpcStatus"];
                    };
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

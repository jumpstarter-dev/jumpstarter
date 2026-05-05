// generated from client/v1/client.swagger.json — do not edit by hand
export interface paths {
    "/client/v1/{lease.name}": {
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
        patch: operations["ClientService_UpdateLease"];
        trace?: never;
    };
    "/client/v1/{name_1}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["ClientService_GetLease"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/client/v1/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["ClientService_GetExporter"];
        put?: never;
        post?: never;
        delete: operations["ClientService_DeleteLease"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/client/v1/{parent}/exporters": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["ClientService_ListExporters"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/client/v1/{parent}/leases": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["ClientService_ListLeases"];
        put?: never;
        post: operations["ClientService_CreateLease"];
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
        v1Condition: {
            type?: string;
            status?: string;
            /** Format: int64 */
            observedGeneration?: string;
            lastTransitionTime?: components["schemas"]["v1Time"];
            reason?: string;
            message?: string;
        };
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
         * @description Lease is the canonical Jumpstarter Lease resource shape. It mirrors the
         *     Lease CRD (controller/api/v1alpha1/lease_types.go).
         */
        v1Lease: {
            name?: string;
            /**
             * @description Either selector or exporter_name must be set. The combination is
             *     intentionally not enforced as REQUIRED here so the message can also
             *     carry an empty selector when exporter_name is the chosen pin.
             */
            selector?: string;
            duration?: string;
            readonly effectiveDuration?: string;
            /** Format: date-time */
            beginTime?: string;
            /** Format: date-time */
            readonly effectiveBeginTime?: string;
            /** Format: date-time */
            endTime?: string;
            /** Format: date-time */
            readonly effectiveEndTime?: string;
            readonly client?: string;
            readonly exporter?: string;
            readonly conditions?: components["schemas"]["v1Condition"][];
            exporterName?: string;
            tags?: {
                [key: string]: string;
            };
            /**
             * @description Admin-surface extensions. Numbered >= 14 so client.v1 wire format is
             *     unchanged for deployed jmp CLI binaries; older clients ignore them.
             */
            readonly ended?: boolean;
            labels?: {
                [key: string]: string;
            };
            metadata?: components["schemas"]["v1ResourceMetadata"];
        };
        v1LeaseListResponse: {
            leases?: components["schemas"]["v1Lease"][];
            nextPageToken?: string;
        };
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
        /** Reference: https://github.com/kubernetes/kubernetes/blob/v1.31.1/staging/src/k8s.io/apimachinery/pkg/apis/meta/v1/generated.proto */
        v1Time: {
            /** Format: int64 */
            seconds?: string;
            /** Format: int32 */
            nanos?: number;
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
    ClientService_UpdateLease: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                "lease.name": string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    /**
                     * @description Either selector or exporter_name must be set. The combination is
                     *     intentionally not enforced as REQUIRED here so the message can also
                     *     carry an empty selector when exporter_name is the chosen pin.
                     */
                    selector?: string;
                    duration?: string;
                    readonly effectiveDuration?: string;
                    /** Format: date-time */
                    beginTime?: string;
                    /** Format: date-time */
                    readonly effectiveBeginTime?: string;
                    /** Format: date-time */
                    endTime?: string;
                    /** Format: date-time */
                    readonly effectiveEndTime?: string;
                    readonly client?: string;
                    readonly exporter?: string;
                    readonly conditions?: components["schemas"]["v1Condition"][];
                    exporterName?: string;
                    tags?: {
                        [key: string]: string;
                    };
                    /**
                     * @description Admin-surface extensions. Numbered >= 14 so client.v1 wire format is
                     *     unchanged for deployed jmp CLI binaries; older clients ignore them.
                     */
                    readonly ended?: boolean;
                    labels?: {
                        [key: string]: string;
                    };
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
                    "application/json": components["schemas"]["v1Lease"];
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
    ClientService_GetLease: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /**
                 * @description Resource name in the form "namespaces/{namespace}/{plural}/{id}",
                 *     e.g. "namespaces/lab-foo/leases/abcd1234".
                 */
                name_1: string;
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
                    "application/json": components["schemas"]["v1Lease"];
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
    ClientService_GetExporter: {
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
    ClientService_DeleteLease: {
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
    ClientService_ListExporters: {
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
    ClientService_ListLeases: {
        parameters: {
            query?: {
                pageSize?: number;
                pageToken?: string;
                filter?: string;
                onlyActive?: boolean;
                tagFilter?: string;
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
                    "application/json": components["schemas"]["v1LeaseListResponse"];
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
    ClientService_CreateLease: {
        parameters: {
            query?: {
                leaseId?: string;
            };
            header?: never;
            path: {
                parent: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["v1Lease"];
            };
        };
        responses: {
            /** @description A successful response. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["v1Lease"];
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

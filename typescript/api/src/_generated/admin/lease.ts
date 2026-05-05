// generated from admin/v1/lease.swagger.json — do not edit by hand
export interface paths {
    "/admin/v1/{lease.name}": {
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
        patch: operations["LeaseService_UpdateLease"];
        trace?: never;
    };
    "/admin/v1/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["LeaseService_GetLease"];
        put?: never;
        post?: never;
        delete: operations["LeaseService_DeleteLease"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/{parent}/leases": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["LeaseService_ListLeases"];
        put?: never;
        post: operations["LeaseService_CreateLease"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/{parent}/leases:watch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["LeaseService_WatchLeases"];
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
        v1Condition: {
            type?: string;
            status?: string;
            /** Format: int64 */
            observedGeneration?: string;
            lastTransitionTime?: components["schemas"]["v1Time"];
            reason?: string;
            message?: string;
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
        /**
         * @description LeaseEvent envelopes the resource payload with EventType + cursor so
         *     clients can resume after a disconnect. The resource itself is the
         *     shared jumpstarter.v1.Lease — same wire shape as Get/List.
         */
        v1LeaseEvent: {
            eventType?: components["schemas"]["v1EventType"];
            lease?: components["schemas"]["v1Lease"];
            /** @description Always populated; on BOOKMARK this is the only meaningful field. */
            resourceVersion?: string;
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
    LeaseService_UpdateLease: {
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
    LeaseService_GetLease: {
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
    LeaseService_DeleteLease: {
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
    LeaseService_ListLeases: {
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
    LeaseService_CreateLease: {
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
    LeaseService_WatchLeases: {
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
                        result?: components["schemas"]["v1LeaseEvent"];
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

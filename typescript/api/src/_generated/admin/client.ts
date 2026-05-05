// generated from admin/v1/client.swagger.json — do not edit by hand
export interface paths {
    "/admin/v1/{client.name}": {
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
        patch: operations["ClientService_UpdateClient"];
        trace?: never;
    };
    "/admin/v1/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["ClientService_GetClient"];
        put?: never;
        post?: never;
        delete: operations["ClientService_DeleteClient"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/{parent}/clients": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["ClientService_ListClients"];
        put?: never;
        post: operations["ClientService_CreateClient"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/{parent}/clients:watch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["ClientService_WatchClients"];
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
         * @description Client is the canonical Jumpstarter Client resource shape. The Client
         *     resource was previously not represented in client.v1 — it is added here
         *     fresh, so field numbering is unconstrained.
         */
        v1Client: {
            name?: string;
            username?: string;
            labels?: {
                [key: string]: string;
            };
            /**
             * @description All three below are admin-surface only. Optional so a client.v1
             *     consumer (or any caller through a path that doesn't populate them)
             *     sees them absent rather than as empty strings.
             */
            readonly endpoint?: string;
            /**
             * @description token is populated only on CreateClient responses (the bootstrap
             *     credential the controller mints inline) and is never echoed on
             *     Get/List/Update/Watch.
             */
            readonly token?: string;
            metadata?: components["schemas"]["v1ResourceMetadata"];
        };
        v1ClientEvent: {
            eventType?: components["schemas"]["v1EventType"];
            client?: components["schemas"]["v1Client"];
            resourceVersion?: string;
        };
        v1ClientListResponse: {
            clients?: components["schemas"]["v1Client"][];
            nextPageToken?: string;
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
    ClientService_UpdateClient: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                "client.name": string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    username?: string;
                    labels?: {
                        [key: string]: string;
                    };
                    /**
                     * @description All three below are admin-surface only. Optional so a client.v1
                     *     consumer (or any caller through a path that doesn't populate them)
                     *     sees them absent rather than as empty strings.
                     */
                    readonly endpoint?: string;
                    /**
                     * @description token is populated only on CreateClient responses (the bootstrap
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
                    "application/json": components["schemas"]["v1Client"];
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
    ClientService_GetClient: {
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
                    "application/json": components["schemas"]["v1Client"];
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
    ClientService_DeleteClient: {
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
    ClientService_ListClients: {
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
                    "application/json": components["schemas"]["v1ClientListResponse"];
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
    ClientService_CreateClient: {
        parameters: {
            query?: {
                clientId?: string;
            };
            header?: never;
            path: {
                parent: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["v1Client"];
            };
        };
        responses: {
            /** @description A successful response. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["v1Client"];
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
    ClientService_WatchClients: {
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
                        result?: components["schemas"]["v1ClientEvent"];
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

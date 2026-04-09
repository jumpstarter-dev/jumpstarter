import grpc

from .common import ObjectMeta


def call_credentials(kind: str, metadata: ObjectMeta, token: str):
    def metadata_call_credentials(context: grpc.AuthMetadataContext, callback: grpc.AuthMetadataPluginCallback):
        callback(
            [
                ("jumpstarter-kind", kind),
                ("jumpstarter-namespace", metadata.namespace),
                ("jumpstarter-name", metadata.name),
            ],
            None,
        )

    return grpc.composite_call_credentials(
        grpc.metadata_call_credentials(metadata_call_credentials),
        grpc.access_token_call_credentials(token),
    )

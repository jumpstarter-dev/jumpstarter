import os
import sys


def configure_grpc_env():
    # On macOS, native gRPC fork handlers can corrupt active TLS streams during
    # subprocess creation: https://github.com/grpc/grpc/issues/28557. Set this
    # before gRPC initializes, preserving any explicit operator override.
    if sys.platform == "darwin":
        os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")

    # disable informative logs by default, i.e.:
    # WARNING: All log messages before absl::InitializeLog() is called are written to STDERR
    # I0000 00:00:1739970744.889307   61962 ssl_transport_security.cc:1665] Handshake failed ...
    if os.environ.get("GRPC_VERBOSITY") is None:
        os.environ["GRPC_VERBOSITY"] = "ERROR"
    if os.environ.get("GLOG_minloglevel") is None:
        os.environ["GLOG_minloglevel"] = "2"


# Configure gRPC defaults before Jumpstarter imports modules that use gRPC.
configure_grpc_env()

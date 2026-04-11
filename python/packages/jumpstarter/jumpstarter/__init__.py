import os
import platform


def configure_grpc_env():
    # disable informative logs by default, i.e.:
    # WARNING: All log messages before absl::InitializeLog() is called are written to STDERR
    # I0000 00:00:1739970744.889307   61962 ssl_transport_security.cc:1665] Handshake failed ...
    if os.environ.get("GRPC_VERBOSITY") is None:
        os.environ["GRPC_VERBOSITY"] = "ERROR"
    if os.environ.get("GLOG_minloglevel") is None:
        os.environ["GLOG_minloglevel"] = "2"
    # Use native DNS resolver on macOS to avoid c-ares resolution failures
    # with wildcard DNS services like nip.io (common in local Kind/k3s deployments)
    if platform.system() == "Darwin" and os.environ.get("GRPC_DNS_RESOLVER") is None:
        os.environ["GRPC_DNS_RESOLVER"] = "native"


# make sure that the grpc environment is always configured
# before any grpc calls are made to avoid unnecessary logs
configure_grpc_env()

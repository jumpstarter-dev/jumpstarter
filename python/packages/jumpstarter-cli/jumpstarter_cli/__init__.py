import os

import truststore

# if we are running in MacOS avoid injecting system certificates to avoid
# https://github.com/jumpstarter-dev/jumpstarter/issues/362
# also allow to force the system certificates injection with
# the JUMPSTARTER_FORCE_SYSTEM_CERTS environment variable set to "1"
if os.uname().sysname != "Darwin" or os.environ.get("JUMPSTARTER_FORCE_SYSTEM_CERTS") == "1":
    truststore.inject_into_ssl()

# On macOS, gRPC resolves names with getaddrinfo and not with its own resolver (c-ares), unless the
# environment says which. c-ares asks the system for its DNS servers, and a process that was forked and not
# exec'd is told nothing: `jmp run` serves in such a child, which then cannot find its controller ("Could not
# contact DNS servers"). gRPC reads this when it is imported, so it is said here, before anything imports it.
if os.uname().sysname == "Darwin":
    os.environ.setdefault("GRPC_DNS_RESOLVER", "native")

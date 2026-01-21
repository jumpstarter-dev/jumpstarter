import os

import truststore

# if we are running in MacOS avoid injecting system certificates to avoid
# https://github.com/jumpstarter-dev/jumpstarter/issues/362
# also allow to force the system certificates injection with
# JUMPSTARTER_FORCE_SYSTEM_CERTS=1
if os.uname().sysname != "Darwin" or os.environ.get("JUMPSTARTER_FORCE_SYSTEM_CERTS") == "1":
    truststore.inject_into_ssl()

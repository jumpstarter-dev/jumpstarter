proxy.set_state("auth_token", "mock-token-001")
proxy.set_state("retries", 3)

token = proxy.get_state("auth_token")   # "mock-token-001"
all_state = proxy.get_all_state()       # {"auth_token": "...", "retries": 3}

proxy.clear_state()

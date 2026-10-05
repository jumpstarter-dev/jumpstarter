def pytest_addoption(parser):
    parser.addoption("--live-netsim-host", default="localhost")
    parser.addoption("--live-netsim-port", default="7681", type=int)
    parser.addoption("--live-netsim-cli", default="")

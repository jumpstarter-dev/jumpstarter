import stat
import sys

import pytest

from jumpstarter.common.ipaddr import get_minikube_ip


@pytest.fixture
def fake_minikube(tmp_path):
    def make(script: str) -> str:
        path = tmp_path / "minikube"
        path.write_text(f"#!{sys.executable}\nimport sys\n{script}\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)

    return make


class TestGetMinikubeIp:
    @pytest.mark.anyio
    async def test_returns_stripped_stdout(self, fake_minikube):
        minikube = fake_minikube('print("192.168.49.2")')

        assert await get_minikube_ip(minikube=minikube) == "192.168.49.2"

    @pytest.mark.anyio
    async def test_runs_the_ip_subcommand(self, fake_minikube):
        minikube = fake_minikube('print(" ".join(sys.argv[1:]))')

        assert await get_minikube_ip(minikube=minikube) == "ip"

    @pytest.mark.anyio
    async def test_passes_the_profile(self, fake_minikube):
        minikube = fake_minikube('print(" ".join(sys.argv[1:]))')

        assert await get_minikube_ip("test-profile", minikube) == "ip -p test-profile"

    @pytest.mark.anyio
    async def test_failure_raises_with_stderr(self, fake_minikube):
        minikube = fake_minikube('sys.stderr.write("error: cluster not found\\n"); sys.exit(1)')

        with pytest.raises(RuntimeError, match="error: cluster not found"):
            await get_minikube_ip(minikube=minikube)

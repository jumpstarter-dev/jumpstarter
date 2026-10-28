"""Tests for operator-based Jumpstarter installation."""

import json
import os
import stat
import sys

import pytest

from jumpstarter_kubernetes.cluster.operator import apply_jumpstarter_cr
from jumpstarter_kubernetes.exceptions import ClusterOperationError

FAKE_KUBECTL = """\
import json, os, sys
args = sys.argv[1:]
stdin = sys.stdin.read() if args[-2:] == ["-f", "-"] else ""
with open(os.environ["FAKE_KUBECTL_LOG"], "a") as log:
    log.write(json.dumps({"args": args, "stdin": stdin}) + "\\n")
if "create" in args:
    print("apiVersion: v1\\nkind: Namespace\\nmetadata:\\n  name: lab")
elif "apply" in args and os.environ.get("FAKE_KUBECTL_FAIL_APPLY"):
    sys.stderr.write("apply refused\\n")
    sys.exit(1)
"""

NAMESPACE_YAML = "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: lab"


@pytest.fixture
def kubectl_calls(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(f"#!{sys.executable}\n{FAKE_KUBECTL}")
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "kubectl.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_KUBECTL_LOG", str(log))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()]

    return calls


class TestApplyJumpstarterCr:
    @pytest.mark.anyio
    async def test_pipes_namespace_and_cr_yaml_to_kubectl_apply(self, kubectl_calls):
        await apply_jumpstarter_cr("lab", "example.com", "grpc.example.com:443", "router.example.com:443")

        create, apply_namespace, apply_cr = kubectl_calls()
        assert create["args"][:3] == ["create", "namespace", "lab"]
        assert apply_namespace == {"args": ["apply", "-f", "-"], "stdin": NAMESPACE_YAML}
        assert apply_cr["args"] == ["apply", "-f", "-"]
        assert "kind: Jumpstarter" in apply_cr["stdin"]

    @pytest.mark.anyio
    async def test_failed_apply_raises_with_kubectl_stderr(self, kubectl_calls, monkeypatch):
        monkeypatch.setenv("FAKE_KUBECTL_FAIL_APPLY", "1")

        with pytest.raises(ClusterOperationError, match="apply refused"):
            await apply_jumpstarter_cr("lab", "example.com", "grpc.example.com:443", "router.example.com:443")

    @pytest.mark.anyio
    async def test_passes_kubeconfig_and_context_to_every_call(self, kubectl_calls):
        await apply_jumpstarter_cr(
            "lab",
            "example.com",
            "grpc.example.com:443",
            "router.example.com:443",
            kubeconfig="/tmp/kubeconfig",
            context="lab-context",
        )

        for call in kubectl_calls():
            assert call["args"][:4] == ["--kubeconfig", "/tmp/kubeconfig", "--context", "lab-context"]

import json
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RENOVATE_JSON = REPO_ROOT / "renovate.json"


@pytest.fixture
def config():
    with open(RENOVATE_JSON) as f:
        return json.load(f)


@pytest.fixture
def package_rules(config):
    return config.get("packageRules", [])


@pytest.fixture
def kubernetes_rules(package_rules):
    return [r for r in package_rules if r.get("groupName") == "kubernetes"]


class TestBaseConfiguration:
    def test_renovate_json_exists(self):
        assert RENOVATE_JSON.exists(), "renovate.json must exist at repository root"

    def test_valid_json(self):
        with open(RENOVATE_JSON) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_extends_config_recommended(self, config):
        assert "extends" in config
        assert "config:recommended" in config["extends"]

    def test_weekly_schedule(self, config):
        assert "schedule" in config or any(
            "schedule" in r for r in config.get("packageRules", [])
        ), "schedule must be configured"


class TestKubernetesGrouping:
    def test_kubernetes_group_exists(self, kubernetes_rules):
        assert len(kubernetes_rules) > 0, "kubernetes group rule must exist"

    def test_kubernetes_group_matches_k8s_io(self, kubernetes_rules):
        all_patterns = []
        for r in kubernetes_rules:
            all_patterns.extend(r.get("matchPackagePatterns", []))
            all_patterns.extend(r.get("matchPackagePrefixes", []))
        pattern_str = " ".join(all_patterns)
        assert "k8s.io" in pattern_str, "kubernetes group must match k8s.io packages"

    def test_kubernetes_group_matches_controller_runtime(self, kubernetes_rules):
        rule_str = json.dumps(kubernetes_rules)
        assert "controller-runtime" in rule_str, (
            "kubernetes group must include controller-runtime"
        )

    def test_kubernetes_group_matches_cert_manager(self, kubernetes_rules):
        rule_str = json.dumps(kubernetes_rules)
        assert "cert-manager" in rule_str, (
            "kubernetes group must include cert-manager"
        )

    def test_kubernetes_group_uses_gomod_manager(self, kubernetes_rules):
        for r in kubernetes_rules:
            managers = r.get("matchManagers", [])
            assert "gomod" in managers, "kubernetes group must use gomod manager"

    def test_kubernetes_group_not_automerged_for_non_patch(self, package_rules):
        k8s_rules = [r for r in package_rules if r.get("groupName") == "kubernetes"]
        for r in k8s_rules:
            if r.get("matchUpdateTypes") and "patch" not in r.get(
                "matchUpdateTypes", []
            ):
                assert r.get("automerge") is not True, (
                    "kubernetes non-patch must not automerge"
                )
            elif not r.get("matchUpdateTypes"):
                assert r.get("automerge") is not True, (
                    "kubernetes group default must not automerge"
                )


class TestIndependentGoDeps:
    def test_grpc_not_in_kubernetes_group(self, kubernetes_rules):
        rule_str = json.dumps(kubernetes_rules)
        assert "grpc" not in rule_str.lower(), (
            "grpc must not be in kubernetes group"
        )

    def test_gin_not_in_kubernetes_group(self, kubernetes_rules):
        rule_str = json.dumps(kubernetes_rules)
        assert "gin-gonic" not in rule_str, (
            "gin must not be in kubernetes group"
        )

    def _matches_pattern(self, package, patterns):
        for p in patterns:
            regex = p.replace("*", ".*")
            if re.match(regex, package):
                return True
        return False

    def test_k8s_patterns_do_not_match_grpc(self, kubernetes_rules):
        for r in kubernetes_rules:
            patterns = r.get("matchPackagePatterns", [])
            prefixes = r.get("matchPackagePrefixes", [])
            names = r.get("matchPackageNames", [])
            assert "google.golang.org/grpc" not in names
            for prefix in prefixes:
                assert not "google.golang.org/grpc".startswith(prefix), (
                    f"prefix {prefix} matches grpc"
                )


class TestPythonDependencies:
    def test_python_not_disabled(self, config):
        enabled_managers = config.get("enabledManagers", None)
        if enabled_managers is not None:
            assert any(
                m in enabled_managers for m in ["pep621", "pip_requirements", "pip_setup"]
            ), "Python managers must not be disabled"

    def test_no_ignore_python(self, config):
        ignore_paths = config.get("ignorePaths", [])
        for path in ignore_paths:
            assert "python" not in path.lower(), (
                "python/ must not be in ignorePaths"
            )


class TestDockerTracking:
    def test_docker_not_disabled(self, config):
        enabled_managers = config.get("enabledManagers", None)
        if enabled_managers is not None:
            assert "dockerfile" in enabled_managers, (
                "dockerfile manager must not be disabled"
            )

    def test_no_ignore_dockerfiles(self, config):
        ignore_paths = config.get("ignorePaths", [])
        for path in ignore_paths:
            assert "Dockerfile" not in path and "dockerfile" not in path.lower(), (
                "Dockerfiles must not be in ignorePaths"
            )


class TestGitHubActionsGrouping:
    def _get_gha_groups(self, package_rules):
        return [
            r
            for r in package_rules
            if "github-actions" in r.get("matchManagers", [])
            and r.get("groupName")
        ]

    def test_gha_groups_exist(self, package_rules):
        gha_groups = self._get_gha_groups(package_rules)
        assert len(gha_groups) >= 3, (
            "at least 3 GHA organization groups must exist"
        )

    def test_actions_official_group(self, package_rules):
        gha_groups = self._get_gha_groups(package_rules)
        group_str = json.dumps(gha_groups)
        assert "actions/" in group_str, "actions/* group must exist"

    def test_docker_actions_group(self, package_rules):
        gha_groups = self._get_gha_groups(package_rules)
        group_str = json.dumps(gha_groups)
        assert "docker/" in group_str, "docker/* group must exist"

    def test_astral_actions_group(self, package_rules):
        gha_groups = self._get_gha_groups(package_rules)
        group_str = json.dumps(gha_groups)
        assert "astral-sh/" in group_str, "astral-sh/* group must exist"


class TestAutoMergePolicy:
    def test_patch_automerge_rule_exists(self, package_rules):
        patch_automerge = [
            r
            for r in package_rules
            if r.get("automerge") is True
            and "patch" in r.get("matchUpdateTypes", [])
        ]
        assert len(patch_automerge) > 0, "patch automerge rule must exist"

    def test_automerge_type_is_pr(self, package_rules):
        patch_automerge = [
            r
            for r in package_rules
            if r.get("automerge") is True
            and "patch" in r.get("matchUpdateTypes", [])
        ]
        for r in patch_automerge:
            assert r.get("automergeType") == "pr", "automergeType must be pr"

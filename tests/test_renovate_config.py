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

    def test_dependabot_yml_does_not_exist(self):
        dependabot_yml = REPO_ROOT / ".github" / "dependabot.yml"
        assert not dependabot_yml.exists(), (
            "dependabot.yml must not coexist with renovate.json to avoid duplicate PRs"
        )

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

    def test_kubernetes_group_is_single_rule(self, kubernetes_rules):
        assert len(kubernetes_rules) == 1, (
            "kubernetes group must be a single consolidated rule, "
            f"found {len(kubernetes_rules)}"
        )

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

    def test_kubernetes_group_covers_all_go_mod_files(self, kubernetes_rules):
        expected_files = {
            "controller/go.mod",
            "controller/deploy/operator/go.mod",
            "e2e/test/go.mod",
        }
        all_file_names = set()
        for r in kubernetes_rules:
            all_file_names.update(r.get("matchFileNames", []))
        missing = expected_files - all_file_names
        assert not missing, (
            f"kubernetes group matchFileNames is missing: {missing}"
        )

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


class TestGrpcProtobufGrouping:
    @pytest.fixture
    def grpc_protobuf_rules(self, package_rules):
        return [r for r in package_rules if r.get("groupName") == "grpc-protobuf"]

    def test_grpc_protobuf_group_exists(self, grpc_protobuf_rules):
        assert len(grpc_protobuf_rules) > 0, "grpc-protobuf group rule must exist"

    def test_grpc_protobuf_group_is_single_rule(self, grpc_protobuf_rules):
        assert len(grpc_protobuf_rules) == 1, (
            "grpc-protobuf group must be a single consolidated rule, "
            f"found {len(grpc_protobuf_rules)}"
        )

    def test_grpc_protobuf_includes_grpcio(self, grpc_protobuf_rules):
        names = grpc_protobuf_rules[0].get("matchPackageNames", [])
        assert "grpcio" in names, "grpc-protobuf group must include grpcio"

    def test_grpc_protobuf_includes_grpcio_tools(self, grpc_protobuf_rules):
        names = grpc_protobuf_rules[0].get("matchPackageNames", [])
        assert "grpcio-tools" in names, (
            "grpc-protobuf group must include grpcio-tools"
        )

    def test_grpc_protobuf_includes_protobuf(self, grpc_protobuf_rules):
        names = grpc_protobuf_rules[0].get("matchPackageNames", [])
        assert "protobuf" in names, "grpc-protobuf group must include protobuf"

    def test_grpc_protobuf_uses_pep621_manager(self, grpc_protobuf_rules):
        managers = grpc_protobuf_rules[0].get("matchManagers", [])
        assert "pep621" in managers, "grpc-protobuf group must use pep621 manager"


class TestKubernetesPythonGrouping:
    @pytest.fixture
    def kubernetes_python_rules(self, package_rules):
        return [r for r in package_rules if r.get("groupName") == "kubernetes-python"]

    def test_kubernetes_python_group_exists(self, kubernetes_python_rules):
        assert len(kubernetes_python_rules) > 0, (
            "kubernetes-python group rule must exist"
        )

    def test_kubernetes_python_group_is_single_rule(self, kubernetes_python_rules):
        assert len(kubernetes_python_rules) == 1, (
            "kubernetes-python group must be a single consolidated rule, "
            f"found {len(kubernetes_python_rules)}"
        )

    def test_kubernetes_python_includes_kubernetes(self, kubernetes_python_rules):
        names = kubernetes_python_rules[0].get("matchPackageNames", [])
        assert "kubernetes" in names, (
            "kubernetes-python group must include kubernetes"
        )

    def test_kubernetes_python_includes_kubernetes_asyncio(self, kubernetes_python_rules):
        names = kubernetes_python_rules[0].get("matchPackageNames", [])
        assert "kubernetes-asyncio" in names, (
            "kubernetes-python group must include kubernetes-asyncio"
        )

    def test_kubernetes_python_uses_pep621_manager(self, kubernetes_python_rules):
        managers = kubernetes_python_rules[0].get("matchManagers", [])
        assert "pep621" in managers, (
            "kubernetes-python group must use pep621 manager"
        )


class TestGolangVersionTracking:
    @pytest.fixture
    def golang_version_rules(self, package_rules):
        return [r for r in package_rules if r.get("groupName") == "golang-version"]

    def test_golang_version_group_exists(self, golang_version_rules):
        assert len(golang_version_rules) > 0, (
            "golang-version group rule must exist"
        )

    def test_golang_version_group_is_single_rule(self, golang_version_rules):
        assert len(golang_version_rules) == 1, (
            "golang-version group must be a single consolidated rule, "
            f"found {len(golang_version_rules)}"
        )

    def test_golang_version_matches_golang_version_dep_type(self, golang_version_rules):
        dep_types = golang_version_rules[0].get("matchDepTypes", [])
        assert "golang-version" in dep_types, (
            "golang-version group must match golang-version depType"
        )

    def test_golang_version_covers_all_go_mod_files(self, golang_version_rules):
        expected_files = {
            "controller/go.mod",
            "controller/deploy/operator/go.mod",
            "e2e/test/go.mod",
        }
        all_file_names = set()
        for r in golang_version_rules:
            all_file_names.update(r.get("matchFileNames", []))
        missing = expected_files - all_file_names
        assert not missing, (
            f"golang-version group matchFileNames is missing: {missing}"
        )

    def test_golang_version_not_automerged(self, golang_version_rules):
        for r in golang_version_rules:
            assert r.get("automerge") is not True, (
                "golang-version group must not automerge"
            )


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

    def test_docker_images_not_automerged(self, package_rules):
        docker_rules = [
            r
            for r in package_rules
            if r.get("automerge") is False
            and "docker" in json.dumps(r.get("matchManagers", [])).lower()
        ]
        assert len(docker_rules) > 0, (
            "a rule must explicitly disable automerge for Docker image updates"
        )

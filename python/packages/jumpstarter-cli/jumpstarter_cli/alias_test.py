from click.testing import CliRunner

from .jmp import jmp


class TestUserCliNounAliases:
    def test_get_exporter_resolves_to_get_exporters(self):
        runner = CliRunner()
        result_plural = runner.invoke(jmp, ["get", "exporters", "--help"])
        result_singular = runner.invoke(jmp, ["get", "exporter", "--help"])
        assert result_singular.exit_code == 0
        assert result_plural.exit_code == 0
        assert result_singular.output == result_plural.output

    def test_get_lease_resolves_to_get_leases(self):
        runner = CliRunner()
        result_plural = runner.invoke(jmp, ["get", "leases", "--help"])
        result_singular = runner.invoke(jmp, ["get", "lease", "--help"])
        assert result_singular.exit_code == 0
        assert result_plural.exit_code == 0
        assert result_singular.output == result_plural.output

    def test_delete_lease_resolves_to_delete_leases(self):
        runner = CliRunner()
        result_plural = runner.invoke(jmp, ["delete", "leases", "--help"])
        result_singular = runner.invoke(jmp, ["delete", "lease", "--help"])
        assert result_singular.exit_code == 0
        assert result_plural.exit_code == 0
        assert result_singular.output == result_plural.output

    def test_create_leases_resolves_to_create_lease(self):
        runner = CliRunner()
        result_singular = runner.invoke(jmp, ["create", "lease", "--help"])
        result_plural = runner.invoke(jmp, ["create", "leases", "--help"])
        assert result_plural.exit_code == 0
        assert result_singular.exit_code == 0
        assert result_singular.output == result_plural.output

    def test_update_leases_resolves_to_update_lease(self):
        runner = CliRunner()
        result_singular = runner.invoke(jmp, ["update", "lease", "--help"])
        result_plural = runner.invoke(jmp, ["update", "leases", "--help"])
        assert result_plural.exit_code == 0
        assert result_singular.exit_code == 0
        assert result_singular.output == result_plural.output

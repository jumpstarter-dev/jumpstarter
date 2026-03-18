from click.testing import CliRunner

from jumpstarter_cli_admin import admin


class TestAdminGetNounAliases:
    def test_get_clients_resolves_to_get_client(self):
        runner = CliRunner()
        result_singular = runner.invoke(admin, ["get", "client", "--help"])
        result_plural = runner.invoke(admin, ["get", "clients", "--help"])
        assert result_singular.exit_code == 0
        assert result_plural.exit_code == 0
        assert result_singular.output == result_plural.output

    def test_get_exporters_resolves_to_get_exporter(self):
        runner = CliRunner()
        result_singular = runner.invoke(admin, ["get", "exporter", "--help"])
        result_plural = runner.invoke(admin, ["get", "exporters", "--help"])
        assert result_singular.exit_code == 0
        assert result_plural.exit_code == 0
        assert result_singular.output == result_plural.output

    def test_get_cluster_and_clusters_remain_distinct(self):
        runner = CliRunner()
        result_singular = runner.invoke(admin, ["get", "cluster", "--help"])
        result_plural = runner.invoke(admin, ["get", "clusters", "--help"])
        assert result_singular.exit_code == 0
        assert result_plural.exit_code == 0
        assert result_singular.output != result_plural.output


class TestAdminCreateNounAliases:
    def test_create_clusters_resolves_to_create_cluster(self):
        runner = CliRunner()
        result_singular = runner.invoke(admin, ["create", "cluster", "--help"])
        result_plural = runner.invoke(admin, ["create", "clusters", "--help"])
        assert result_singular.exit_code == 0
        assert result_plural.exit_code == 0
        assert result_singular.output == result_plural.output


class TestAdminDeleteNounAliases:
    def test_delete_clusters_resolves_to_delete_cluster(self):
        runner = CliRunner()
        result_singular = runner.invoke(admin, ["delete", "cluster", "--help"])
        result_plural = runner.invoke(admin, ["delete", "clusters", "--help"])
        assert result_singular.exit_code == 0
        assert result_plural.exit_code == 0
        assert result_singular.output == result_plural.output

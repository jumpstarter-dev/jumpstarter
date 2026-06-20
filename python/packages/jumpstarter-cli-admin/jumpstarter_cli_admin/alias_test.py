import click

from jumpstarter_cli_admin import admin


def _subgroup(name):
    """Resolve admin's `name` subgroup + a child context for command lookup."""
    group = admin.get_command(click.Context(admin), name)
    return group, click.Context(group, parent=click.Context(admin))


class TestAdminGetNounAliases:
    # The subcommands forward to the Rust CLI; assert the AliasedGroup *resolution*
    # rather than the (Rust-printed, uncaptured) output.
    def test_get_clients_resolves_to_get_client(self):
        group, ctx = _subgroup("get")
        assert group.get_command(ctx, "clients") is group.get_command(ctx, "client")

    def test_get_exporters_resolves_to_get_exporter(self):
        group, ctx = _subgroup("get")
        assert group.get_command(ctx, "exporters") is group.get_command(ctx, "exporter")

    def test_get_cluster_and_clusters_remain_distinct(self):
        group, ctx = _subgroup("get")
        cluster = group.get_command(ctx, "cluster")
        clusters = group.get_command(ctx, "clusters")
        assert cluster is not None and clusters is not None
        assert cluster.name == "cluster"
        assert clusters.name == "clusters"
        assert cluster is not clusters


class TestAdminCreateNounAliases:
    def test_create_clusters_resolves_to_create_cluster(self):
        group, ctx = _subgroup("create")
        assert group.get_command(ctx, "clusters") is group.get_command(ctx, "cluster")


class TestAdminDeleteNounAliases:
    def test_delete_clusters_resolves_to_delete_cluster(self):
        group, ctx = _subgroup("delete")
        assert group.get_command(ctx, "clusters") is group.get_command(ctx, "cluster")

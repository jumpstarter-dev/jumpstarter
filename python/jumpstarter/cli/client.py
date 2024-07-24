import click


@click.group
def client():
    """Manage client configs"""
    pass


@client.command
@click.argument("name")
def create(name):
    """Create client config"""
    pass


@client.command
@click.argument("name")
def use(name):
    """Use client config"""
    pass


@client.command
@click.argument("name")
def delete(name):
    """Delete client config"""
    pass

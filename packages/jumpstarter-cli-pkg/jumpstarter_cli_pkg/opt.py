import asyncclick as click

opt_drivers = click.option("--drivers", is_flag=True, help="Print drivers only.")

opt_driver_clients = click.option("--driver-clients", is_flag=True, help="Print driver clients only.")

opt_adapters = click.option("--adapters", is_flag=True, help="Print adapters only.")

opt_inspect = click.option("--inspect", "-i", is_flag=True, help="Inspect the packages to get additional metadata.")

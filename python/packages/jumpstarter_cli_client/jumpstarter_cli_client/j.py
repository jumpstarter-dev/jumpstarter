from jumpstarter.common.utils import env


def main():
    with env() as client:
        client.cli()(standalone_mode=False)


if __name__ == "__main__":
    main()

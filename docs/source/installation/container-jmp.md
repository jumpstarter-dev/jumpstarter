# Container package

For interacting with the Jumpstarter service without installing the Python
packages locally, you can create an alias to run the `jmp` client in a container.

```{tip}
It is recommended to add the alias to your shell profile.
```

```bash
$ alias jmp='podman run --rm -it -e JUMPSTARTER_GRPC_INSECURE=1 \
              -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter" \
               quay.io/jumpstarter-dev/jumpstarter:main jmp'
```

Then you can try:

```bash
$ jmp client list
CURRENT   NAME      ENDPOINT                         PATH
*         default   grpc.devel.jumpstarter.dev:443   /root/.config/jumpstarter/clients/default.yaml
          kirkb     grpc.devel.jumpstarter.dev:443   /root/.config/jumpstarter/clients/kirkb.yaml
```

## Hardware Access for exporters

If you need access to your hardware, i.e. because you are running the `exporter`
or you are following the `local workflow` (without a distributed service), you need
to mount access to devices into the container, provide host network access,
and run the container in privileged mode, this probably needs to be run as **root**.


```bash
$ mkdir -p "${HOME}/.config/jumpstarter/"

# you may want to add this alias to the profile
$ alias jmp='podman run --rm -it -e JUMPSTARTER_GRPC_INSECURE=1 \
              -v "${HOME}/.config/jumpstarter/:/root/.config/jumpstarter" \
              --net=host  --privileged \
              -v /run/udev:/run/udev -v /dev:/dev -v /etc/jumpstarter:/etc/jumpstarter \
              quay.io/jumpstarter-dev/jumpstarter:main jmp'
```


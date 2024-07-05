#!/usr/bin/env bash


if which systemctl; then

    if [ -f /etc/systemd/system/user@.service.d/delegate.conf ]; then
        echo "Kind systemd rootless already configured" && exit 0
    else
        echo "Configuring Kind for rootless operation in Linux"
        # Enable rootless Kind, see https://kind.sigs.k8s.io/docs/user/rootless/
        sudo mkdir -p /etc/systemd/system/user@.service.d
        cat << EOF | sudo tee /etc/systemd/system/user@.service.d/delegate.conf > /dev/null
[Service]
Delegate=yes
EOF

        sudo systemctl daemon-reload
    fi
fi


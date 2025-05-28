setup_file() {
  # create clients
  jmp admin create client   test-client-oidc     --unsafe --out /dev/null \
    --oidc-username dex:test-client-oidc
  jmp admin create client   test-client-sa       --unsafe --out /dev/null \
    --oidc-username dex:system:serviceaccount:default:test-client-sa
  jmp admin create client   test-client-legacy   --unsafe --save

  # create exporters
  jmp admin create exporter test-exporter-oidc   --out /dev/null \
    --oidc-username dex:test-exporter-oidc \
    --label example.com/board=oidc
  jmp admin create exporter test-exporter-sa     --out /dev/null \
    --oidc-username dex:system:serviceaccount:default:test-exporter-sa \
    --label example.com/board=sa
  jmp admin create exporter test-exporter-legacy --save \
    --label example.com/board=legacy
}

teardown_file() {
  # delete clients
  jmp admin delete client   test-client-oidc
  jmp admin delete client   test-client-sa
  jmp admin delete client   test-client-legacy

  # delete exporters
  jmp admin delete exporter test-exporter-oidc
  jmp admin delete exporter test-exporter-sa
  jmp admin delete exporter test-exporter-legacy
}

@test "can run our script" {
  jmp config client   list
  jmp config exporter list

  jmp login --client test-client-oidc \
    --endpoint "$ENDPOINT" --namespace default --name test-client-oidc \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --username test-client-oidc@example.com --password password --unsafe

  jmp login --client test-client-sa \
    --endpoint "$ENDPOINT" --namespace default --name test-client-sa \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --connector-id kubernetes \
    --token $(kubectl create -n default token test-client-sa) --unsafe

  jmp login --exporter test-exporter-oidc \
    --endpoint "$ENDPOINT" --namespace default --name test-exporter-oidc \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --username test-exporter-oidc@example.com --password password

  jmp login --exporter test-exporter-sa \
    --endpoint "$ENDPOINT" --namespace default --name test-exporter-sa \
    --issuer https://dex.dex.svc.cluster.local:5556 \
    --connector-id kubernetes \
    --token $(kubectl create -n default token test-exporter-sa)

  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"$GITHUB_ACTION_PATH/exporter.yaml\")" \
    /etc/jumpstarter/exporters/test-exporter-oidc.yaml
  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"$GITHUB_ACTION_PATH/exporter.yaml\")" \
    /etc/jumpstarter/exporters/test-exporter-sa.yaml
  go run github.com/mikefarah/yq/v4@latest -i ". * load(\"$GITHUB_ACTION_PATH/exporter.yaml\")" \
    /etc/jumpstarter/exporters/test-exporter-legacy.yaml

  jmp config client list
  jmp config exporter list

  jmp run --exporter test-exporter-oidc &
  jmp run --exporter test-exporter-sa &
  jmp run --exporter test-exporter-legacy &

  kubectl -n default wait --for=condition=Online --for=condition=Registered \
    exporters.jumpstarter.dev/test-exporter-oidc
  kubectl -n default wait --for=condition=Online --for=condition=Registered \
    exporters.jumpstarter.dev/test-exporter-sa
  kubectl -n default wait --for=condition=Online --for=condition=Registered \
    exporters.jumpstarter.dev/test-exporter-legacy

  jmp config client use test-client-oidc

  jmp create lease     --selector example.com/board=oidc --duration 1d
  jmp get    leases
  jmp get    exporters
  jmp delete leases    --all

  jmp admin get client
  jmp admin get exporter
  jmp admin get lease

  jmp run --exporter test-exporter-oidc &
  kubectl -n default wait --for=condition=Online --for=condition=Registered \
    exporters.jumpstarter.dev/test-exporter-oidc

  jmp shell --client test-client-oidc   --selector example.com/board=oidc   j power on
  jmp shell --client test-client-sa     --selector example.com/board=sa     j power on
  jmp shell --client test-client-legacy --selector example.com/board=legacy j power on

  kubectl -n default get secret test-client-oidc-client
  kubectl -n default get secret test-exporter-oidc-exporter

  jmp admin delete client   test-client-oidc -d
  jmp admin delete exporter test-exporter-oidc -d

  ! kubectl -n default get secret test-client-oidc-client
  ! kubectl -n default get secret test-exporter-oidc-exporter
}

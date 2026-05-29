podman build -t jumpstarter-mitmproxy:latest .

podman run --rm -it --privileged \
  -v /dev:/dev \
  -v /etc/jumpstarter:/etc/jumpstarter:Z \
  -p 8080:8080 -p 8081:8081 \
  jumpstarter-mitmproxy:latest \
  jmp exporter start my-bench

package main

import (
	"log"
	"net"
	"os"

	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/router"
	"google.golang.org/grpc"
)

func main() {
	address := "/tmp/jumpstarter-router.sock"

	os.RemoveAll(address)

	listen, err := net.Listen("unix", address)
	if err != nil {
		log.Fatal(err)
	}

	server := grpc.NewServer()

	rs, err := router.NewRouterServer(&router.RouterConfig{})
	if err != nil {
		log.Fatal(err)
	}

	pb.RegisterRouterServiceServer(server, rs)

	err = server.Serve(listen)
	if err != nil {
		log.Fatal(err)
	}
}

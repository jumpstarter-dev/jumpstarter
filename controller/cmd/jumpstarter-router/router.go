package main

import (
	"flag"
	"log"
	"net"

	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/controller"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/router"
	"google.golang.org/grpc"
)

var listenAddr = flag.String("listen", "127.0.0.1:8000", "listen address")

func main() {
	flag.Parse()

	listen, err := net.Listen("tcp", *listenAddr)
	if err != nil {
		log.Fatal(err)
	}

	server := grpc.NewServer()

	// controllerPrincipal := uuid.New().String()
	// controllerKey := uuid.New().String()
	// routerPrincipal := uuid.New().String()
	// routerKey := uuid.New().String()

	cs, err := controller.NewControllerServer(&controller.ControllerConfig{})
	if err != nil {
		log.Fatal(err)
	}

	pb.RegisterControllerServiceServer(server, cs)

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

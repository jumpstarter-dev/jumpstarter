package main

import (
	"flag"
	"log"
	"net"

	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/controller"
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

	cs, err := controller.NewControllerServer(&controller.ControllerConfig{})
	if err != nil {
		log.Fatal(err)
	}

	pb.RegisterControllerServiceServer(server, cs)

	err = server.Serve(listen)
	if err != nil {
		log.Fatal(err)
	}
}

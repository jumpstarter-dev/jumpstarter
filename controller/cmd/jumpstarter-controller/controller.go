package main

import (
	"log"
	"net"
	"os"

	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/controller"
	"google.golang.org/grpc"
)

func main() {
	address := "/tmp/jumpstarter-controller.sock"

	os.RemoveAll(address)

	listen, err := net.Listen("unix", address)
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

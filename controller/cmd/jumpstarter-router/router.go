package main

import (
	"flag"
	"log"
	"net"

	"github.com/google/uuid"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/controller"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/router"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/stream"
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

	controllerPrincipal := uuid.New().String()
	controllerKey := uuid.New().String()
	routerPrincipal := uuid.New().String()
	routerKey := uuid.New().String()
	streamPrincipal := uuid.New().String()

	cs, err := controller.NewControllerServer(&controller.ControllerConfig{
		Principal:  controllerPrincipal,
		PrivateKey: controllerKey,
		Router: struct {
			Principal string
		}{
			Principal: routerPrincipal,
		},
	})
	if err != nil {
		log.Fatal(err)
	}

	pb.RegisterControllerServiceServer(server, cs)

	rs, err := router.NewRouterServer(&router.RouterConfig{
		Principal:  routerPrincipal,
		PrivateKey: routerKey,
		Controller: struct {
			PublicKey string
			Principal string
		}{
			PublicKey: controllerKey,
			Principal: controllerPrincipal,
		},
		Stream: struct {
			Principal string
		}{
			Principal: streamPrincipal,
		},
	})
	if err != nil {
		log.Fatal(err)
	}

	pb.RegisterRouterServiceServer(server, rs)

	ss, err := stream.NewStreamServer(&stream.StreamConfig{
		Principal: streamPrincipal,
		Router: struct {
			PublicKey string
			Principal string
		}{
			PublicKey: routerKey,
			Principal: routerPrincipal,
		},
	})
	if err != nil {
		log.Fatal(err)
	}

	pb.RegisterStreamServiceServer(server, ss)

	err = server.Serve(listen)
	if err != nil {
		log.Fatal(err)
	}
}

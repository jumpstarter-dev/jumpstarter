package main

import (
	"flag"
	"log"
	"net"

	"github.com/google/uuid"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
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

	streamPrincipal := uuid.New().String()
	streamKey := uuid.New().String()
	routerPrincipal := uuid.New().String()

	rs, err := router.NewRouterServer(&router.RouterConfig{
		Principal: routerPrincipal,
		Controller: struct {
			PublicKey string
			Principal string
		}{
			PublicKey: "controller",
			Principal: "controller",
		},
		Stream: struct {
			PrivateKey string
			Principal  string
		}{
			PrivateKey: streamKey,
			Principal:  streamPrincipal,
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
			PublicKey: streamKey,
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

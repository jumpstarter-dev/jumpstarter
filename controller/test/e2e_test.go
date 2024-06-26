package test

import (
	"context"
	"net"
	"os"
	"testing"
	"time"

	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/authn"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/controller"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/router"
	"golang.org/x/oauth2"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/local"
	"google.golang.org/grpc/credentials/oauth"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	apicorev1 "k8s.io/client-go/kubernetes/typed/core/v1"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/envtest"
)

func createServiceAccount(
	t *testing.T,
	client apicorev1.ServiceAccountInterface,
	name string,
) *corev1.ServiceAccount {
	sa, err := client.Create(
		context.TODO(),
		&corev1.ServiceAccount{
			ObjectMeta: metav1.ObjectMeta{
				Name: name,
			},
		},
		metav1.CreateOptions{},
	)
	if err != nil {
		t.Fatalf("failed to create exporter service account: %s", err)
	}
	return sa
}

func prepareControler(clientset *kubernetes.Clientset) (func() error, error) {
	address := "/tmp/jumpstarter-controller.sock"

	os.RemoveAll(address)

	listen, err := net.Listen("unix", address)
	if err != nil {
		return nil, err
	}

	cs, err := controller.NewControllerServer(clientset)
	if err != nil {
		return nil, err
	}

	server := grpc.NewServer()

	pb.RegisterControllerServiceServer(server, cs)

	return func() error {
		return server.Serve(listen)
	}, nil
}

func prepareRouter(config *rest.Config) (func() error, error) {
	address := "/tmp/jumpstarter-router.sock"

	os.RemoveAll(address)

	listen, err := net.Listen("unix", address)
	if err != nil {
		return nil, err
	}

	server := grpc.NewServer()

	err = router.RegisterRouterServer(server, config)
	if err != nil {
		return nil, err
	}

	return func() error {
		return server.Serve(listen)
	}, nil
}

func TestController(t *testing.T) {
	env := &envtest.Environment{}

	cfg, err := env.Start()
	if err != nil {
		t.Fatalf("failed to start envtest: %s", err)
	}

	clientset, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		t.Fatalf("failed to create k8s client: %s", err)
	}

	saclient := clientset.CoreV1().ServiceAccounts(corev1.NamespaceDefault)

	controllerFunc, err := prepareControler(clientset)
	if err != nil {
		t.Fatalf("failed to create prepare controller: %s", err)
	}

	go controllerFunc()

	routerFunc, err := prepareRouter(cfg)
	if err != nil {
		t.Fatalf("failed to create prepare router: %s", err)
	}

	go routerFunc()

	_ = createServiceAccount(t, saclient, "jumpstarter-tokenholder")

	exporterToken, err := authn.Issue(
		context.TODO(),
		saclient,
		"jumpstarter-controller.example.com",
		authn.TokenParam{
			Subject:    "exporter-01",
			Expiration: time.Now().Add(time.Hour),
		},
	)
	if err != nil {
		t.Fatalf("failed to issue exporter token: %s", err)
	}

	clientToken, err := authn.Issue(
		context.TODO(),
		saclient,
		"jumpstarter-controller.example.com",
		authn.TokenParam{
			Subject:    "client-01",
			Expiration: time.Now().Add(time.Hour),
		},
	)
	if err != nil {
		t.Fatalf("failed to issue client token: %s", err)
	}

	clientGrpc, err := grpc.NewClient(
		"unix:/tmp/jumpstarter-controller.sock",
		grpc.WithTransportCredentials(local.NewCredentials()),
		grpc.WithPerRPCCredentials(oauth.TokenSource{TokenSource: oauth2.StaticTokenSource(&oauth2.Token{
			AccessToken: clientToken,
		})}),
	)

	client := pb.NewControllerServiceClient(clientGrpc)

	exporterGrpc, err := grpc.NewClient(
		"unix:/tmp/jumpstarter-controller.sock",
		grpc.WithTransportCredentials(local.NewCredentials()),
		grpc.WithPerRPCCredentials(oauth.TokenSource{TokenSource: oauth2.StaticTokenSource(&oauth2.Token{
			AccessToken: exporterToken,
		})}),
	)

	exporter := pb.NewControllerServiceClient(exporterGrpc)

	{
		resp, err := client.Dial(context.TODO(), &pb.DialRequest{Uuid: "exporter-01"})
		t.Log("client dial", resp, err)

		resp, err = exporter.Dial(context.TODO(), &pb.DialRequest{Uuid: "exporter-01"})
		t.Log("exporter dial", resp, err)
	}

	{
		resp, err := client.Listen(context.TODO(), &pb.ListenRequest{})
		t.Log("client listen", resp, err)

		resp, err = exporter.Listen(context.TODO(), &pb.ListenRequest{})
		t.Log("exporter listen", resp, err)
	}

	env.Stop()
}

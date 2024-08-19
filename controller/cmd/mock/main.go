package main

import (
	"log"
	"net"
	"os"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/controller"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/service"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"google.golang.org/grpc"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

var (
	scheme = runtime.NewScheme()
)

const (
	// to make sure we are not hardcoding namespaces in code
	namespace = "81c6ed4dc0bf88203081454aefa806ca"
)

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(jumpstarterdevv1alpha1.AddToScheme(scheme))

	_ = os.Setenv("NAMESPACE", namespace)
	_ = os.Setenv("CONTROLLER_KEY", "dummy")
	_ = os.Setenv("ROUTER_KEY", "dummy")
}

func main() {
	server := grpc.NewServer()

	exporter := jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "exporter-sample",
			Namespace: namespace,
		},
		Status: jumpstarterdevv1alpha1.ExporterStatus{
			Credential: &corev1.LocalObjectReference{
				Name: "exporter-sample-token",
			},
		},
	}

	exporterToken, err := controller.SignObjectToken(
		"https://jumpstarter.dev/controller",
		[]string{"https://jumpstarter.dev/controller"},
		&exporter,
		scheme,
	)
	utilruntime.Must(err)

  log.Println("exporter token:", exporterToken)

	client := jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "identity-sample",
			Namespace: namespace,
		},
		Status: jumpstarterdevv1alpha1.ClientStatus{
			Credential: &corev1.LocalObjectReference{
				Name: "identity-sample-token",
			},
		},
	}

	clientToken, err := controller.SignObjectToken(
		"https://jumpstarter.dev/controller",
		[]string{"https://jumpstarter.dev/controller"},
		&client,
		scheme,
	)
	utilruntime.Must(err)

	log.Println("client token:", clientToken)

	c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(
		&exporter,
		&client,
	).WithStatusSubresource(&exporter).Build()

	pb.RegisterControllerServiceServer(server, &service.ControllerService{
		Client: c,
		Scheme: scheme,
	})

	pb.RegisterRouterServiceServer(server, &service.RouterService{
		Client: c,
		Scheme: scheme,
	})

	listener, err := net.Listen("tcp", ":8083")
	if err != nil {
		log.Fatal(err)
	}

	log.Fatal(server.Serve(listener))
}

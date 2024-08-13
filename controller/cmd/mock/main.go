package main

import (
	"log"
	"net"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
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

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(jumpstarterdevv1alpha1.AddToScheme(scheme))
}

func main() {
	server := grpc.NewServer()

	exporter := jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "exporter-sample",
			Namespace: "default",
		},
		Spec: jumpstarterdevv1alpha1.ExporterSpec{
			Credentials: []corev1.SecretReference{{
				Name:      "exporter-sample-token",
				Namespace: "default",
			}},
		},
	}

	client := fake.NewClientBuilder().WithScheme(scheme).WithObjects(
		&exporter,
		&jumpstarterdevv1alpha1.Identity{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "identity-sample",
				Namespace: "default",
			},
			Spec: jumpstarterdevv1alpha1.IdentitySpec{
				Credentials: []corev1.SecretReference{{
					Name:      "identity-sample-token",
					Namespace: "default",
				}},
			},
		},
		&corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "exporter-sample-token",
				Namespace: "default",
			},
			Data: map[string][]byte{
				"token": []byte("54d8cd395728888be9fcb93c4575d99e"),
			},
		},
		&corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "identity-sample-token",
				Namespace: "default",
			},
			Data: map[string][]byte{
				"token": []byte("fc5c6dda1083a69e9886dc160de5b44e"),
			},
		},
	).WithStatusSubresource(&exporter).Build()

	pb.RegisterControllerServiceServer(server, &service.ControllerService{
		Client: client,
		Scheme: scheme,
	})

	pb.RegisterRouterServiceServer(server, &service.RouterService{
		Client: client,
		Scheme: scheme,
	})

	listener, err := net.Listen("tcp", ":8083")
	if err != nil {
		log.Fatal(err)
	}

	log.Fatal(server.Serve(listener))
}

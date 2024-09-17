package cmd

import (
	"context"
	"os"
	"time"

	"path/filepath"

	"github.com/spf13/cobra"
	"sigs.k8s.io/controller-runtime/pkg/client"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/util/homedir"
)

var (
	kubeconfig string
	namespace  string
	timeout    string
)

func init() {
	utilruntime.Must(jumpstarterdevv1alpha1.AddToScheme(scheme.Scheme))

	rootCmd.PersistentFlags().StringVar(&kubeconfig, "kubeconfig", filepath.Join(homedir.HomeDir(), ".kube", "config"), "Path to the kubeconfig file to use")
	rootCmd.PersistentFlags().StringVar(&namespace, "namespace", "default", "Kubernetes namespace to operate on")
	rootCmd.PersistentFlags().StringVar(&timeout, "timeout", "10s", "command timeout")
}

func NewClient() (client.WithWatch, error) {
	config, err := clientcmd.BuildConfigFromFlags("", kubeconfig)
	if err != nil {
		return nil, err
	}
	return client.NewWithWatch(config, client.Options{Scheme: scheme.Scheme})
}

type contextKey string

var (
	rootCmd = &cobra.Command{
		Use:          "jmpctl",
		Short:        "Admin CLI for managing jumpstarter",
		SilenceUsage: true,
		PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
			duration, err := time.ParseDuration(timeout)
			if err != nil {
				return err
			}

			ctx, cancel := context.WithTimeout(context.Background(), duration)
			ctx = context.WithValue(ctx, contextKey("cancel"), cancel)
			cmd.SetContext(ctx)

			return nil
		},
		PersistentPostRunE: func(cmd *cobra.Command, args []string) error {
			cmd.Context().Value(contextKey("cancel")).(context.CancelFunc)()

			return nil
		},
	}
)

func Execute() {
	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}

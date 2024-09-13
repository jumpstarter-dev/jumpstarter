package cmd

import (
	"context"
	"os"
	"path/filepath"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/spf13/cobra"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/cli-runtime/pkg/printers"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/util/homedir"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

var (
	kubeconfig string
	namespace  string
)

func init() {
	jumpstarterdevv1alpha1.AddToScheme(scheme.Scheme)

	rootCmd.AddCommand(exporterCmd)

	exporterCmd.PersistentFlags().StringVar(&kubeconfig, "kubeconfig", filepath.Join(homedir.HomeDir(), ".kube", "config"), "Path to the kubeconfig file to use")
	exporterCmd.PersistentFlags().StringVar(&namespace, "namespace", "default", "Kubernetes namespace to operate on")
	exporterCmd.AddCommand(exporterCreateCmd)
	exporterCmd.AddCommand(exporterDeleteCmd)
	exporterCmd.AddCommand(exporterListCmd)
}

var exporterCmd = &cobra.Command{
	Use:   "exporter",
	Short: "Manage exporters",
}

func NewClient() (client.Client, error) {
	config, err := clientcmd.BuildConfigFromFlags("", kubeconfig)
	if err != nil {
		return nil, err
	}
	return client.New(config, client.Options{Scheme: scheme.Scheme})
}

var exporterCreateCmd = &cobra.Command{
	Use:   "create [NAME]",
	Short: "Create exporter",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		clientset, err := NewClient()
		if err != nil {
			return err
		}
		exporter := jumpstarterdevv1alpha1.Exporter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      args[0],
				Namespace: namespace,
			},
		}
		return clientset.Create(context.Background(), &exporter)
	},
}

var exporterDeleteCmd = &cobra.Command{
	Use:   "delete [NAME]",
	Short: "Delete exporter",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		clientset, err := NewClient()
		if err != nil {
			return err
		}
		var exporter jumpstarterdevv1alpha1.Exporter
		if err := clientset.Get(context.Background(), types.NamespacedName{
			Namespace: namespace,
			Name:      args[0],
		}, &exporter); err != nil {
			return err
		}
		return clientset.Delete(context.Background(), &exporter)
	},
}

var exporterListCmd = &cobra.Command{
	Use:   "list",
	Short: "List exporters",
	RunE: func(cmd *cobra.Command, args []string) error {
		clientset, err := NewClient()
		if err != nil {
			return err
		}
		var exporters jumpstarterdevv1alpha1.ExporterList
		if err := clientset.List(context.Background(), &exporters, &client.ListOptions{Namespace: namespace}); err != nil {
			return err
		}
		printers.NewTablePrinter(printers.PrintOptions{}).PrintObj(&exporters, os.Stdout)
		return nil
	},
}

package cmd

import (
	"fmt"
	"os"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v2"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/cli-runtime/pkg/printers"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

func init() {
	rootCmd.AddCommand(clientCmd)

	clientCmd.AddCommand(clientCreateCmd)
	clientCmd.AddCommand(clientDeleteCmd)
	clientCmd.AddCommand(clientListCmd)
}

var clientCmd = &cobra.Command{
	Use:   "client",
	Short: "Manage clients",
}

var clientCreateCmd = &cobra.Command{
	Use:   "create [NAME]",
	Short: "Create client",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := cmd.Context()

		clientset, err := NewClient()
		if err != nil {
			return err
		}
		client := jumpstarterdevv1alpha1.Client{
			ObjectMeta: metav1.ObjectMeta{
				Name:      args[0],
				Namespace: namespace,
			},
		}
		if err := clientset.Create(ctx, &client); err != nil {
			return err
		}
		watch, err := clientset.Watch(ctx, &jumpstarterdevv1alpha1.ClientList{}, &kclient.ListOptions{
			FieldSelector: fields.OneTermEqualSelector("metadata.name", args[0]),
			Namespace:     namespace,
		})
		if err != nil {
			return err
		}
		for event := range watch.ResultChan() {
			object := event.Object.(*jumpstarterdevv1alpha1.Client)
			if object.Status.Credential == nil || object.Status.Endpoint == "" {
				continue
			}
			var secret corev1.Secret
			if err := clientset.Get(
				ctx,
				types.NamespacedName{Name: object.Status.Credential.Name, Namespace: namespace},
				&secret,
			); err != nil {
				return err
			}
			if secret.Data == nil {
				return fmt.Errorf("Empty Secret on Client %s/%s", namespace, args[0])
			}
			token, ok := secret.Data["token"]
			if !ok {
				return fmt.Errorf("Missing token in Secret for Client %s/%s", namespace, args[0])
			}
			clientConfig := []yaml.MapItem{
				{
					Key:   "apiVersion",
					Value: "jumpstarter.dev/v1alpha1",
				},
				{
					Key:   "kind",
					Value: "ClientConfig",
				},
				{
					Key:   "endpoint",
					Value: object.Status.Endpoint,
				},
				{
					Key:   "token",
					Value: string(token),
				},
			}
			if err := yaml.NewEncoder(os.Stdout).Encode(&clientConfig); err != nil {
				return err
			}
			watch.Stop()
			return nil
		}
		return fmt.Errorf("timout waiting for controller to update status for Client: %s", args[0])
	},
}

var clientDeleteCmd = &cobra.Command{
	Use:   "delete [NAME]",
	Short: "Delete client",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := cmd.Context()

		clientset, err := NewClient()
		if err != nil {
			return err
		}
		var client jumpstarterdevv1alpha1.Client
		if err := clientset.Get(ctx, types.NamespacedName{
			Namespace: namespace,
			Name:      args[0],
		}, &client); err != nil {
			return err
		}
		return clientset.Delete(ctx, &client)
	},
}

var clientListCmd = &cobra.Command{
	Use:   "list",
	Short: "List clients",
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := cmd.Context()

		clientset, err := NewClient()
		if err != nil {
			return err
		}
		var clients jumpstarterdevv1alpha1.ClientList
		if err := clientset.List(ctx, &clients, &kclient.ListOptions{Namespace: namespace}); err != nil {
			return err
		}
		return printers.NewTablePrinter(printers.PrintOptions{}).PrintObj(&clients, os.Stdout)
	},
}

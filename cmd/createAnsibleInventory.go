/*
Copyright © 2023 Miguel Angel Ajo Pelayo <majopela@redhat.com
*/
package cmd

import (
	"os"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"github.com/jumpstarter-dev/jumpstarter/pkg/tools"
	"github.com/spf13/cobra"
)

// powerCmd represents the listDevices command
var createAnsibleInventory = &cobra.Command{
	Use:   "create-ansible-inventory",
	Short: "Create an ansible inventory file",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(1)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		driver := cmd.Flag("driver").Value.String()
		user := cmd.Flag("user").Value.String()
		sshKey := cmd.Flag("ssh-key").Value.String()

		device, err := harness.FindDevice(driver, args[0])
		handleErrorAsFatal(err)
		tools.CreateAnsibleInventory(device, os.Stdout, user, sshKey)
	},
}

func init() {
	rootCmd.AddCommand(createAnsibleInventory)
	createAnsibleInventory.Flags().StringP("driver", "d", "", "Only devices for the specified driver")
	createAnsibleInventory.Flags().StringP("user", "u", "root", "The user for the ansible inventory file")
	createAnsibleInventory.Flags().StringP("ssh-key", "k", "", "The ssh key to use for the ansible inventory file")
}

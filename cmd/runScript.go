/*
Copyright © 2023 Miguel Angel Ajo Pelayo <majopela@redhat.com
*/
package cmd

import (
	"github.com/jumpstarter-dev/jumpstarter/pkg/runner"
	"github.com/spf13/cobra"
)

// powerCmd represents the listDevices command
var runScript = &cobra.Command{
	Use:   "run-script",
	Short: "Run a jumpstarter script",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(1)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		script := args[0]
		driver := cmd.Flag("driver").Value.String()
		disableCleanup, _ := cmd.Flags().GetBool("disable-cleanup")

		err := runner.RunScript("", driver, script, disableCleanup)
		handleErrorAsFatal(err)
	},
}

func init() {
	rootCmd.AddCommand(runScript)
	runScript.Flags().StringP("driver", "d", "", "Only run on devices for the specified driver")
	runScript.Flags().BoolP("disable-cleanup", "c", false, "Disable the cleanup phase if something goes wrong")
}

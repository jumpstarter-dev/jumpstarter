/*
Copyright © 2023 Miguel Angel Ajo Pelayo <majopela@redhat.com
*/
package cmd

import (
	"fmt"
	"strings"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"github.com/jumpstarter-dev/jumpstarter/pkg/tools"
	"github.com/spf13/cobra"
)

// powerCmd represents the listDevices command
var runCmd = &cobra.Command{
	Use:   "run",
	Short: "Run a command via console",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(2)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		driver := cmd.Flag("driver").Value.String()
		wait, err := cmd.Flags().GetInt("wait")
		handleErrorAsFatal(err)

		device, err := harness.FindDevice(driver, args[0])
		serial, err := device.Console()
		handleErrorAsFatal(err)

		result, err := tools.RunCommand(serial, strings.Join(args[1:], " "), wait)
		handleErrorAsFatal(err)
		fmt.Println(result)
	},
}

func init() {
	rootCmd.AddCommand(runCmd)
	runCmd.Flags().StringP("driver", "d", "", "Only devices for the specified driver")
	runCmd.Flags().IntP("wait", "w", 2, "Wait seconds before trying to get a response")
}

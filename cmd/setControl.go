/*
Copyright © 2023 Miguel Angel Ajo Pelayo <majopela@redhat.com
*/
package cmd

import (
	"fmt"

	"github.com/fatih/color"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"github.com/spf13/cobra"
)

// powerCmd represents the listDevices command
var setControlCmd = &cobra.Command{
	Use:   "set-control",
	Short: "Set a control signal from the test-harness to the device",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(3)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		deviceId, key, value := args[0], args[1], args[2]
		driver := cmd.Flag("driver").Value.String()
		device, err := harness.FindDevice(driver, deviceId)
		handleErrorAsFatal(err)

		color.Set(COLOR_CMD_INFO)
		fmt.Printf("⚡ Setting control signal %s to %s on %s\n", key, value, deviceId)
		color.Unset()
		err = device.SetControl(key, value)
		handleErrorAsFatal(err)

	},
}

func init() {
	rootCmd.AddCommand(setControlCmd)
	setControlCmd.Flags().StringP("driver", "d", "", "Only list devices for the specified driver")

}

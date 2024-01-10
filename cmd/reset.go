/*
Copyright © 2023 Miguel Angel Ajo Pelayo <majopela@redhat.com
*/
package cmd

import (
	"fmt"
	"time"

	"github.com/fatih/color"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"github.com/spf13/cobra"
)

// powerCmd represents the listDevices command
var resetCmd = &cobra.Command{
	Use:   "reset",
	Short: "use the reset signal on the device to reset it, only open drain signals are supported (pulling low + high impedance)",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(1)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		deviceId := args[0]
		driver := cmd.Flag("driver").Value.String()
		device, err := harness.FindDevice(driver, deviceId)
		handleErrorAsFatal(err)

		resetDevice(device, deviceId)

	},
}

func init() {
	rootCmd.AddCommand(resetCmd)
	resetCmd.Flags().StringP("driver", "d", "", "Only list devices for the specified driver")

}

func resetDevice(device harness.Device, id string) {
	color.Set(COLOR_CMD_INFO)
	fmt.Printf("⚡ Toggling reset on %s\n", id)
	color.Unset()
	err := device.SetControl("reset", "low")
	handleErrorAsFatal(err)

	time.Sleep(1 * time.Second)
	err = device.SetControl("reset", "z")
	handleErrorAsFatal(err)
}

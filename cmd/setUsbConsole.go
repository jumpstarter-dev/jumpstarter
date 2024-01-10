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
var setUsbConsole = &cobra.Command{
	Use:   "set-usb-console",
	Short: "Changes device name for out of band USB console",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(1)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		driver := cmd.Flag("driver").Value.String()
		device, err := harness.FindDevice(driver, args[0])
		handleErrorAsFatal(err)
		color.Set(COLOR_CMD_INFO)
		fmt.Printf("✍ Changing usb_console name for %s to %s ... ", args[0], args[1])
		color.Unset()

		err = device.SetUsbConsole(args[1])
		handleErrorAsFatal(err)

		color.Set(COLOR_CMD_INFO)
		fmt.Println("done")
		color.Unset()
	},
}

func init() {
	rootCmd.AddCommand(setUsbConsole)
	setUsbConsole.Flags().StringP("driver", "d", "", "Only list devices for the specified driver")
}

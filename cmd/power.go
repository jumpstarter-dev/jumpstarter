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
var powerCmd = &cobra.Command{
	Use:   "power",
	Short: "Powers control for devices",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(2)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		action := args[0]
		device_id := args[1]

		driver := cmd.Flag("driver").Value.String()
		console, _ := cmd.Flags().GetBool("console")
		reset, _ := cmd.Flags().GetBool("reset")
		attach_storage, _ := cmd.Flags().GetBool("attach-storage")

		device, err := harness.FindDevice(driver, device_id)
		handleErrorAsFatal(err)

		color.Set(COLOR_CMD_INFO)
		fmt.Printf("🔌 Power action	%s on %s ... ", action, device_id)
		color.Unset()

		err = device.Power(action)
		handleErrorAsFatal(err)

		color.Set(COLOR_CMD_INFO)
		fmt.Println("done")
		color.Unset()

		if attach_storage {
			color.Set(COLOR_CMD_INFO)
			fmt.Printf("💾 Attaching storage for %s ... ", device_id)
			color.Unset()
			err = device.AttachStorage(true)
			handleErrorAsFatal(err)
			color.Set(COLOR_CMD_INFO)
			fmt.Println("done")
			color.Unset()
		}

		if reset {
			resetDevice(device, args[0])
		}

		if console {
			serialConsole(device)
		}
	},
}

func init() {
	rootCmd.AddCommand(powerCmd)
	powerCmd.Flags().StringP("driver", "d", "", "Only list devices for the specified driver")
	powerCmd.Flags().BoolP("console", "c", false, "Open console terminal after powering on")
	powerCmd.Flags().BoolP("reset", "r", false, "Reset device after power up")
	powerCmd.Flags().BoolP("attach-storage", "a", false, "Attach storage before powering on")
}

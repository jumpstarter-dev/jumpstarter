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
var powerOnCmd = &cobra.Command{
	Use:   "power-on",
	Short: "Powers device on",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(1)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		driver := cmd.Flag("driver").Value.String()
		console, _ := cmd.Flags().GetBool("console")
		pwcycle, _ := cmd.Flags().GetBool("cycle")
		reset, _ := cmd.Flags().GetBool("reset")
		attach_storage, _ := cmd.Flags().GetBool("attach-storage")

		device, err := harness.FindDevice(driver, args[0])
		handleErrorAsFatal(err)

		color.Set(COLOR_CMD_INFO)
		if pwcycle {
			fmt.Printf("🔌 Power cycling %s... ", args[0])
			err = device.Power("off")
			handleErrorAsFatal(err)
			time.Sleep(2 * time.Second)
		} else {
			fmt.Printf("🔌 Powering on %s... ", args[0])
		}
		color.Unset()

		err = device.Power("on")
		handleErrorAsFatal(err)

		color.Set(COLOR_CMD_INFO)
		fmt.Println("done")
		color.Unset()

		if attach_storage {
			color.Set(COLOR_CMD_INFO)
			fmt.Printf("💾 Attaching storage for %s ... ", args[0])
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
	rootCmd.AddCommand(powerOnCmd)
	powerOnCmd.Flags().StringP("driver", "d", "", "Only list devices for the specified driver")
	powerOnCmd.Flags().BoolP("console", "t", false, "Open console terminal after powering on")
	powerOnCmd.Flags().BoolP("cycle", "c", false, "Power cycle the device")
	powerOnCmd.Flags().BoolP("reset", "r", false, "Reset device after power up")
	powerOnCmd.Flags().BoolP("attach-storage", "a", false, "Attach storage before powering on")
}

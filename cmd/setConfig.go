/*
Copyright © 2023 Miguel Angel Ajo Pelayo <majopela@redhat.com
*/
package cmd

import (
	"fmt"
	"strings"

	"github.com/fatih/color"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"github.com/spf13/cobra"
)

// powerCmd represents the listDevices command
var setConfig = &cobra.Command{
	Use:   "set-config",
	Short: "Changes a device config parameter",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(3)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		device_id := args[0]
		k := strings.ToLower(args[1])
		v := args[2]

		driver := cmd.Flag("driver").Value.String()
		device, err := harness.FindDevice(driver, device_id)
		handleErrorAsFatal(err)
		color.Set(COLOR_CMD_INFO)
		fmt.Printf("✍ Changing device setting for %s > %s=%s ... ", device_id, k, v)
		color.Unset()

		err = device.SetConfig(k, v)
		handleErrorAsFatal(err)

		color.Set(COLOR_CMD_INFO)
		fmt.Println("done")
		color.Unset()
	},
}

func init() {
	rootCmd.AddCommand(setConfig)
	setConfig.Flags().StringP("driver", "d", "", "Only list devices for the specified driver")
}

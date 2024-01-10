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
var setDiskImageCmd = &cobra.Command{
	Use:   "set-disk-image",
	Short: "Writes a disk image to the target device attached storage",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(2)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		driver := cmd.Flag("driver").Value.String()
		offset, err := cmd.Flags().GetUint("offset-gb")
		handleErrorAsFatal(err)

		device, err := harness.FindDevice(driver, args[0])
		handleErrorAsFatal(err)

		color.Set(COLOR_CMD_INFO)
		fmt.Printf("💾 Writing disk image for %s\n", args[0])
		color.Unset()
		err = device.SetDiskImage(args[1], uint64(offset)*1024*1024*1024)
		handleErrorAsFatal(err)

	},
}

func init() {
	rootCmd.AddCommand(setDiskImageCmd)
	setDiskImageCmd.Flags().StringP("driver", "d", "", "Only list devices for the specified driver")
	setDiskImageCmd.Flags().UintP("offset-gb", "o", 0, "Offset in GB to write the image to in the disk")
	// add fixed possition argument device-id

}

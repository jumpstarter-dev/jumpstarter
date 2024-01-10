/*
Copyright © 2023 Miguel Angel Ajo Pelayo <majopela@redhat.com>
*/
package cmd

import (
	"fmt"
	"strings"

	"github.com/fatih/color"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"github.com/spf13/cobra"
)

// listDevicesCmd represents the listDevices command
var listDevicesCmd = &cobra.Command{
	Use:   "list-devices",
	Short: "Lists available devices",
	Long:  `Iterates over the available drivers and gets a list of devices.`,
	Run: func(cmd *cobra.Command, args []string) {
		driver := cmd.Flag("driver").Value.String()
		tags, err := cmd.Flags().GetStringArray("tag")
		handleErrorAsFatal(err)

		devices, err := harness.FindDevices(driver, tags)
		handleErrorAsFatal(err)
		if cmd.Flag("only-names").Value.String() == "true" {
			printDeviceNames(devices)
			return
		}
		printDeviceTable(devices)

	},
}

func init() {
	rootCmd.AddCommand(listDevicesCmd)
	listDevicesCmd.Flags().StringP("driver", "d", "", "Only list devices for the specified driver")
	listDevicesCmd.Flags().Bool("only-names", false, "Only list the device names")
	listDevicesCmd.Flags().StringArrayP("tag", "t", []string{}, "Only list devices with the specified tag(s) can be used multiple times")
}

func printDeviceTable(devices []harness.Device) {
	color.Set(color.FgGreen)
	fmt.Println("Device Name\tSerial Number\tDriver\t\t\tVersion\tDevice\t\tTags")
	color.Unset()
	for _, device := range devices {
		deviceName := device.Name()
		deviceSerial, err := device.Serial()
		handleErrorAsFatal(err)
		deviceVersion, err := device.Version()
		handleErrorAsFatal(err)
		dev, err := device.Device()
		handleErrorAsFatal(err)
		tags := device.Tags()
		str_tags := strings.Join(tags, ", ")

		fmt.Printf("%s\t%s\t%s\t%s\t%s\t%s\n",
			deviceName, deviceSerial, device.Driver().Name(), deviceVersion, dev, str_tags)
	}
}

func printDeviceNames(devices []harness.Device) {
	for _, device := range devices {
		deviceName := device.Name()
		fmt.Printf("%s\n", deviceName)
	}
}

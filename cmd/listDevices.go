/*
Copyright © 2023 Miguel Angel Ajo Pelayo <majopela@redhat.com>
*/
package cmd

import (
	"fmt"
	"strings"

	"github.com/jedib0t/go-pretty/table"
	"github.com/jedib0t/go-pretty/text"
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

		devices, _, err := harness.FindDevices(driver, tags)
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
	t := table.NewWriter()

	t.AppendHeader(table.Row{"Device Name", "Serial Number", "Driver", "Version", "Device", "Tags"})

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

		t.AppendRow([]interface{}{deviceName, deviceSerial, device.Driver().Name(), deviceVersion, dev, str_tags})
	}

	t.SetStyle(table.Style{
		Name: "myNewStyle",
		Box: table.BoxStyle{
			BottomLeft:       "+",
			BottomRight:      "+",
			BottomSeparator:  "+",
			Left:             "|",
			LeftSeparator:    "+",
			MiddleHorizontal: "-",
			MiddleSeparator:  "+",
			MiddleVertical:   "|",
			PaddingLeft:      " ",
			PaddingRight:     " ",
			Right:            "|",
			RightSeparator:   "+",
			TopLeft:          "+",
			TopRight:         "+",
			TopSeparator:     "+",
			UnfinishedRow:    " ~",
		},
		Color: table.ColorOptions{
			Header:      text.Colors{text.FgGreen},
			IndexColumn: text.Colors{text.FgGreen},
		},
		// Options: table.Options{
		// 	DrawBorder:      true,
		// 	SeparateColumns: true,
		// 	SeparateFooter:  true,
		// 	SeparateHeader:  true,
		// 	SeparateRows:    true,
		// },
	})

	fmt.Println(t.Render())
}

func printDeviceNames(devices []harness.Device) {
	for _, device := range devices {
		deviceName := device.Name()
		fmt.Printf("%s\n", deviceName)
	}
}

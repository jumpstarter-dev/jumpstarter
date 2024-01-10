/*
Copyright © 2023 Miguel Angel Ajo Pelayo <majopela@redhat.com
*/
package cmd

import (
	"fmt"
	"os"
	"strings"

	"github.com/fatih/color"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"github.com/spf13/cobra"
)

// powerCmd represents the listDevices command
var getConfig = &cobra.Command{
	Use:   "get-config",
	Short: "Changes a device config parameter",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(1)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		device_id := args[0]
		k := ""
		if len(args) > 1 {
			k = strings.ToLower(args[1])
		}

		driver := cmd.Flag("driver").Value.String()
		device, err := harness.FindDevice(driver, device_id)
		handleErrorAsFatal(err)

		cfg, err := device.GetConfig()

		handleErrorAsFatal(err)

		if k != "" {
			if v, ok := cfg[k]; ok {
				fmt.Println(v)
			} else {
				color.Set(color.FgRed)
				fmt.Println("Not found")
				color.Unset()
				os.Exit(1)
			}
		} else {
			for k, v := range cfg {
				fmt.Printf("%s: %s\n", k, v)
			}
		}
	},
}

func init() {
	rootCmd.AddCommand(getConfig)
	getConfig.Flags().StringP("driver", "d", "", "Only list devices for the specified driver")
}

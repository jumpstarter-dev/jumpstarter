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
var setTags = &cobra.Command{
	Use:   "set-tags",
	Short: "Changes device tags, pass one argument per tag",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(2)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		driver := cmd.Flag("driver").Value.String()
		device, err := harness.FindDevice(driver, args[0])
		handleErrorAsFatal(err)
		color.Set(COLOR_CMD_INFO)
		tags := cleanTags(args[1:])
		fmt.Printf("✍ Changing device tags for %s to %v ... ", args[0], tags)
		color.Unset()

		err = device.SetTags(tags)
		handleErrorAsFatal(err)

		color.Set(COLOR_CMD_INFO)
		fmt.Println("done")
		color.Unset()
	},
}

func cleanTags(tags []string) []string {
	var cleanTags []string
	for _, tag := range tags {
		if tag != "" {
			clean := strings.TrimRight(strings.TrimLeft(tag, ", "), ", ")
			clean = strings.ToLower(clean)
			cleanTags = append(cleanTags, clean)
		}
	}
	return cleanTags
}
func init() {
	rootCmd.AddCommand(setTags)
	setTags.Flags().StringP("driver", "d", "", "Only list devices for the specified driver")
}

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

// listDriversCmd represents the listDrivers command
var listDriversCmd = &cobra.Command{
	Use:   "list-drivers",
	Short: "List available HIL drivers",
	Run: func(cmd *cobra.Command, args []string) {
		drivers := harness.GetDrivers()
		for _, driver := range drivers {
			color.Set(color.FgGreen)
			fmt.Println(driver.Name())
			color.Unset()
			fmt.Printf("\t%s\n", driver.Description())
		}
	},
}

func init() {
	rootCmd.AddCommand(listDriversCmd)
}

package cmd

import "github.com/spf13/cobra"

var (
	rootCmd = &cobra.Command{
		Use:   "jmpctl",
		Short: "Admin CLI for managing jumpstarter",
	}
)

func Execute() error {
	return rootCmd.Execute()
}

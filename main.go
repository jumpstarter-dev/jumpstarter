/*
Copyright © 2023 NAME HERE <EMAIL ADDRESS>
*/
package main

import (
	"github.com/jumpstarter-dev/jumpstarter/cmd"
	_ "github.com/jumpstarter-dev/jumpstarter/pkg/drivers/dutlink-board" // make sure the driver is registered
)

func main() {
	cmd.Execute()
}

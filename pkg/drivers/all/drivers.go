package all

import (
	_ "github.com/jumpstarter-dev/jumpstarter/pkg/drivers/dutlink-board"
	_ "github.com/jumpstarter-dev/jumpstarter/pkg/drivers/mock"
)

// The purpose of this package is to import all the drivers so that they are
// registered and available to the rest of the codebase.

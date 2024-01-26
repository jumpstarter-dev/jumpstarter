package locking

import (
	"fmt"
	"os"
	"path/filepath"
	"syscall"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

const JUMPSTARTER_LOCK_BASE = "/tmp/jumpstarter-locks/"

func init() {
	if _, err := os.Stat(JUMPSTARTER_LOCK_BASE); os.IsNotExist(err) {
		umask := syscall.Umask(0)
		err := os.Mkdir(JUMPSTARTER_LOCK_BASE, 0777)
		syscall.Umask(umask)

		if err != nil {
			fmt.Printf("Error creating %s\n", JUMPSTARTER_LOCK_BASE)
			os.Exit(1)
		}
	}
}

type Lock struct {
	fd       int
	fileName string
}

func TryLock(devicePath string) (Lock, error) {
	baseName := filepath.Base(devicePath)
	fileLock := JUMPSTARTER_LOCK_BASE + "LCK.." + baseName

	umask := syscall.Umask(0)
	fd, err := syscall.Open(fileLock, syscall.O_CREAT|syscall.O_RDONLY, 0666)
	syscall.Umask(umask)
	if err != nil {
		return Lock{}, fmt.Errorf("TryLock: opening %q:  %w", fileLock, err)
	}

	err = syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB)

	if err == syscall.EWOULDBLOCK {
		syscall.Close(fd)
		return Lock{}, harness.ErrDeviceInUse
	}

	if err != nil {
		syscall.Close(fd)
	}

	return Lock{fd, fileLock}, nil
}

func (l *Lock) Unlock() error {
	err := syscall.Flock(l.fd, syscall.LOCK_UN)
	if err != nil {
		return fmt.Errorf("Unlock: %w", err)
	}
	err = syscall.Close(l.fd)
	if err != nil {
		return fmt.Errorf("Unlock: %w", err)
	}
	err = os.Remove(l.fileName)
	if err != nil {
		return fmt.Errorf("Unlock: %w", err)
	}
	return nil
}

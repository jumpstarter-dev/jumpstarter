package storage

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"os/exec"
	"time"

	"github.com/fatih/color"
	"github.com/schollz/progressbar/v3"
)

const WAIT_TIME_USB_STORAGE = 12 * time.Second
const WAIT_TIME_USB_STORAGE_DISCONNECT = 6 * time.Second

func WriteImageToDisk(imagePath string, diskPath string, offset uint64, blockSize uint64, eject bool) error {
	inputFile, err := os.OpenFile(imagePath, os.O_RDONLY, 0666)
	if err != nil {
		return fmt.Errorf("WriteImageToDisk: %w", err)
	}
	defer inputFile.Close()

	fi, err := inputFile.Stat()
	if err != nil {
		return fmt.Errorf("WriteImageToDisk: reading input file info %w", err)
	}
	inputSize := fi.Size()

	outputFile, err := os.OpenFile(diskPath, os.O_WRONLY|os.O_SYNC, 0666)
	if err != nil {
		return fmt.Errorf("WriteImageToDisk: %w", err)
	}

	if _, err := outputFile.Seek(int64(offset), 0); err != nil {
		outputFile.Close()
		return fmt.Errorf("writeImageToDisk:Seek to 0x%x %w", offset, err)
	}

	buffer := make([]byte, blockSize)

	bar := progressbar.DefaultBytes(inputSize, "💾 writing")
	for {
		n, err := inputFile.Read(buffer)
		if err != nil && err != io.EOF {
			outputFile.Close()
			return fmt.Errorf("WriteImageToDisk: %w", err)
		}
		if n == 0 {
			break
		}
		if _, err := outputFile.Write(buffer[:n]); err != nil {
			outputFile.Close()
			return fmt.Errorf("WriteImageToDisk: %w", err)
		}
		bar.Add(n)
	}
	outputFile.Close()
	fmt.Println()

	if err := exec.Command("sync").Run(); err != nil {
		return fmt.Errorf("WriteImageToDisk: sync %w", err)
	}
	if eject {
		fmt.Println("⏏ Requesting disk ejection ....")
		time.Sleep(WAIT_TIME_USB_STORAGE)
		cmd := exec.Command("udisksctl", "power-off", "-b", diskPath)
		var errb bytes.Buffer
		cmd.Stderr = &errb
		if err := cmd.Run(); err != nil {
			// udiskctl doesn't work in the container workflows, so we ignore the error and write a warning
			color.Set(color.FgYellow)
			fmt.Printf("warning: udisksctl power-off failed: %s\n", errb.String())
			color.Unset()
		}
	}
	fmt.Println("🕐 Waiting before disconnecting disk ....")
	time.Sleep(WAIT_TIME_USB_STORAGE_DISCONNECT)
	return nil
}

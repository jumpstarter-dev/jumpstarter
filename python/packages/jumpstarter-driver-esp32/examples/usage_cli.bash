# Flash MicroPython firmware
j storage flash firmware.bin --address 0x1000

# Get chip info
j storage chip-info

# Enter download mode
j storage bootloader

# Erase entire flash
j storage erase

# Hard reset
j storage reset

# Open serial console
j serial start-console

# Read serial output
j serial pipe

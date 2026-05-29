# Log serial output to stdout
j serial pipe

# Log serial output to a file
j serial pipe -o serial.log

# Send command to serial, then continue monitoring output
echo "hello" | j serial pipe

# Send commands from file, then continue monitoring output
cat commands.txt | j serial pipe -o serial.log

# Force bidirectional mode (interactive)
j serial pipe -i

# Append to log file instead of overwriting
j serial pipe -o serial.log -a

# Disable stdin input even when piped
cat data.txt | j serial pipe --no-input

# Fire-and-forget: send stdin to serial and exit at EOF (no serial output)
cat commands.txt | j serial pipe --no-output

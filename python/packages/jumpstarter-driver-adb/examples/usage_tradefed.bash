# Terminal 1: Start the tunnel
j adb tunnel
# Note the port, e.g. 54321

# Terminal 2: Run tradefed with the tunnel port
export ANDROID_ADB_SERVER_PORT=54321
tradefed.sh

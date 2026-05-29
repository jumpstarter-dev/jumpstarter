# List devices
j adb devices

# Interactive shell
j adb shell

# Run a command on the device
j adb shell getprop ro.product.model

# Install an app
j adb install app.apk

# View device logs
j adb logcat

# Push/pull files
j adb push local_file.txt /sdcard/
j adb pull /sdcard/remote_file.txt .

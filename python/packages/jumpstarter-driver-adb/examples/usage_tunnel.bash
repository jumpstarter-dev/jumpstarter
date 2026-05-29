# Create a persistent ADB tunnel (auto-assigned port)
j adb tunnel

# Create a tunnel on a specific port
j adb tunnel -P 5038

# Background the tunnel for continued shell use
j adb tunnel &

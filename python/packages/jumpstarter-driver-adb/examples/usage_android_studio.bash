adb kill-server
j adb tunnel -P 5037
# Note: Android Studio may restart the ADB server on 5037 when opened,
# causing a conflict. If this happens, use the auto-assigned port instead.

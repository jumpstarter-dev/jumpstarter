# Apple Silicon (arm64)
sdkmanager "system-images;android-35;google_apis;arm64-v8a"
avdmanager create avd -n Pixel_6 -k "system-images;android-35;google_apis;arm64-v8a" -d pixel_6

# Intel/AMD (x86_64)
sdkmanager "system-images;android-35;google_apis;x86_64"
avdmanager create avd -n Pixel_6 -k "system-images;android-35;google_apis;x86_64" -d pixel_6

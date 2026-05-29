# Mount remote filesystem (spawns a subshell; type 'exit' to unmount)
j mount /local/mountpoint
j mount /local/mountpoint -r /remote/path
j mount /local/mountpoint --direct

# Mount in foreground mode (blocks until Ctrl+C)
j mount /local/mountpoint --foreground

# Pass extra sshfs options (-o forwards each value as an sshfs -o flag)
j mount /local/mountpoint -o reconnect -o cache=yes

# Disable host key verification (trust-on-first-use is the default)
j mount /local/mountpoint --insecure

# Allow other users to access the mount (requires user_allow_other in /etc/fuse.conf)
j mount /local/mountpoint -o allow_other

# Unmount an orphaned mount
j mount --umount /local/mountpoint
j mount --umount /local/mountpoint --lazy

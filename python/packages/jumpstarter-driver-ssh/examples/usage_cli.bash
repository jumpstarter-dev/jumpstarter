# Basic SSH connection (uses port forwarding by default)
j ssh

# SSH with direct TCP address
j ssh --direct

# SSH with specific user
j ssh -l myuser

# SSH with other flags
j ssh -i ~/.ssh/id_rsa

# Running a remote command
j ssh ls -la

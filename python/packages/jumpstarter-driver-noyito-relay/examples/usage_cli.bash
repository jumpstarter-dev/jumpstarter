# Power on relay 1
j relay1 on

# Query state of relay 1
j relay1 status

# Power cycle relay 2 with a 3-second wait
j relay2 cycle --wait 3

# Power off relay 1
j relay1 off

# Power on all 8 channels simultaneously
j relay_8ch_all on

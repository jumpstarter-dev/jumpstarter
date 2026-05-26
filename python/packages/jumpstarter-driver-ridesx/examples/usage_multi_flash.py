# Flash multiple partitions
partitions = {
    "boot": "/path/to/boot.img",
    "system": "/path/to/system.img",
    "userdata": "/path/to/userdata.img"
}
ridesx_client.flash(partitions)

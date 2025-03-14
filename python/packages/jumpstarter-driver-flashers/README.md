# Jumpstarter Driver for Software driven flashing

Some platforms provide on-board storage which cannot be externally tapped
or flashed. Flashing those devices during testing requires driving
the DUT into the right state through the bootloader or other means so the
storage contents can be transferred through network into the internal storage.

This driver provides a set of base drivers and clients to implement
the FlasherInterface for those target platforms, as well as some specific
implementations.



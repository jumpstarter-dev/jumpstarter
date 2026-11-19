"""The HMI of an Android device: its picture and input, streamed from the exporter by scrcpy's server.

This is the Android implementation of the HMI interface (``jumpstarter-driver-hmi``). It is built by the
``hmi:`` option of :class:`~jumpstarter_driver_adb.driver.AdbDevice` and needs the ``hmi`` extra
(``pip install jumpstarter-driver-adb[hmi]``). A device without the option never imports the HMI package: the
modules that use it (``driver``, ``source``, ``session``, ``input``, ``client`` and the test ``fakes``) are imported
only when an HMI is built or used, and the ones that do not need it (``adb``, ``protocol``, ``server_binary``,
``fetch``) can be used on their own, as the image build does with
``python -m jumpstarter_driver_adb.hmi.fetch``.
"""

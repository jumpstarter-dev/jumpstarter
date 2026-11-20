*** Settings ***
Documentation       Keywords the board's driver author curated.
...
...                 ``MockDutClient`` (``jumpstarter_example_robot/client.py``) marks
...                 ``reboot`` and ``should_be_powered`` with ``@robot.keyword`` and hides
...                 ``raw_status`` with ``@robot.not_keyword``. Like every driver's keywords,
...                 they carry the driver's path, ``dut``: ``Dut Reboot``.

Resource            ../client.resource


*** Test Cases ***
Curated Keywords Are Available
    Dut Reboot    wait=0
    Dut Should Be Powered

Hidden Helpers Are Not Keywords
    ${keywords}=    List Driver Keywords    dut
    Should Contain    ${keywords}    Dut Reboot
    Should Not Contain    ${keywords}    Dut Raw Status

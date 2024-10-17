import logging

log = logging.getLogger(__name__)

def wait_and_login(pexpect_console, username, password, prompt, timeout=240):
    """
    Wait for login prompt and login

    :param pexpect_console: pexpect console object
    :type pexpect_console: pexpect.spawn

    :return: pexpect console object
    :rtype: pexpect.spawn
    """
    log.info("Waiting for login prompt")
    pexpect_console.expect("login:", timeout=timeout)
    pexpect_console.sendline(username)
    pexpect_console.expect("Password:")
    pexpect_console.sendline(password)
    pexpect_console.expect(prompt, timeout=60)
    log.info("Logged in")
    return pexpect_console

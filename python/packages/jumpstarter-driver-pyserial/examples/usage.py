with pyserialclient.pexpect() as session:
    session.sendline("Hello, world!")
    session.expect("Hello, world!")

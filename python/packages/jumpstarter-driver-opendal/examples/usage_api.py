from tempfile import NamedTemporaryFile

opendal.create_dir("test/directory/")
opendal.write_bytes("test/directory/file", b"hello")
assert opendal.hash("test/directory/file", "md5") == "5d41402abc4b2a76b9719d911017c592"
opendal.remove_all("test/")

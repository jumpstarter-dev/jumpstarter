import time

from jumpstarter_driver_tftp.driver import FileNotFound, TftpError

from jumpstarter.common.utils import env


def test_tftp_upload():
    with env() as client:
        try:
            client.tftp.start()
            print("TFTP server started")

            time.sleep(1)

            test_file = "test.bin"
            with open(test_file, "wb") as f:
                f.write(b"Hello from TFTP streaming test!")

            try:
                client.tftp.put_local_file(test_file)
                print(f"Successfully uploaded {test_file}")

                files = client.tftp.list_files()
                print(f"Files in TFTP root: {files}")

                if test_file in files:
                    client.tftp.delete_file(test_file)
                    print(f"Successfully deleted {test_file}")
                else:
                    print(f"Warning: {test_file} not found in TFTP root")

            except TftpError as e:
                print(f"TFTP operation failed: {e}")
            except FileNotFound as e:
                print(f"File not found: {e}")

        except Exception as e:
            print(f"Error: {e}")
        finally:
            try:
                client.tftp.stop()
                print("TFTP server stopped")
            except Exception as e:
                print(f"Error stopping server: {e}")


if __name__ == "__main__":
    test_tftp_upload()

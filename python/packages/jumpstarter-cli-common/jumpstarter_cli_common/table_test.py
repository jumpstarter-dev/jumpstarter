from .table import make_table

EXPECTED_TABLE = """
TEST    HELLO
123456  There
""".lstrip()


def test_make_table():
    COLUMNS = ["TEST", "HELLO"]
    DATA = [{"TEST": "123456", "HELLO": "There"}]
    table = make_table(COLUMNS, DATA)
    assert table == EXPECTED_TABLE

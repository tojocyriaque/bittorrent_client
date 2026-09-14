import json
import bencoding

BENCODING_TESTS = "tests/bencoding.json"


def test_bencoding(tests_path: str):
    print("Testing BENCODING....\n")
    with open(tests_path, "r") as test_file:
        test_cases: dict = json.load(test_file)

    for test_name, test_case in test_cases.items():
        inputs = test_case["input"]
        excpected = test_case["excpected"]

        print(test_name, "processing...")
        output = bencoding.bencode_data(inputs)

        assert (
            output == excpected
        ), f"{test_name} failed !!\nInputs:{inputs}\nExcpected '{excpected}'\nOutput '{output}'\n"
        print(f"{test_name} passed !!\n")
    print("BENCODING passed !!\n")


def test_bdecoding(tests_path: str):
    print("Testing BDECODING....\n")
    with open(tests_path, "r") as test_file:
        test_cases: dict = json.load(test_file)

    for test_name, test_case in test_cases.items():
        excpected = test_case["input"]
        inputs = test_case["excpected"]

        print(test_name, "processing...")
        output, _ = bencoding.bdecode_str(inputs)

        assert (
            output == excpected
        ), f"{test_name} failed !!\nInputs:{inputs}\nExcpected '{excpected}'\nOutput '{output}'\n"
        print(f"{test_name} passed !!\n")
    print("BDECODING passed !!\n")

test_bencoding(BENCODING_TESTS)
print("="*20)
test_bdecoding(BENCODING_TESTS)
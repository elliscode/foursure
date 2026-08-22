def log(*content):
    print(f"{content}")


if __name__ == "__main__":
    log("This is a test message")
    log("This is a test message with some context", {"key1": "group", "key2": "abc123"})

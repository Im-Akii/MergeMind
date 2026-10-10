import os

# Unit/scenario tests use the deterministic lexical backend; the real entire-graph
# backend has its own test (tests/test_entire_backend.py) that runs when the CLI exists.
os.environ.setdefault("MERGEMIND_NO_ENTIRE", "1")

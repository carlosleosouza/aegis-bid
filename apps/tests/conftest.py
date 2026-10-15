import os
import sys

import fakeredis
import pytest

# Permite `import aegis_common`, `import bid`, ... a partir de apps/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)

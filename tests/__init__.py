import os
import tempfile

# Every test run gets a throwaway data folder, never the real ~/.jobhuntai.
os.environ["JOBHUNT_HOME"] = tempfile.mkdtemp(prefix="jobhunt-test-")

# A local Ollama on the developer's PC must not count as a configured AI provider in tests.
from jobhunt import llm  # noqa: E402

llm.ollama_models = lambda timeout=1.0: []

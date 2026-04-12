import sys
from pathlib import Path

# Add the gen/ directory to the Python path so that
# 'jumpstarter_gen' can be imported as a top-level package
sys.path.insert(0, str(Path(__file__).parent / "gen"))

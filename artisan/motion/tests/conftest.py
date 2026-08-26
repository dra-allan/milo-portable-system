import os
import sys

# Make the motion module dir importable so tests import the modules directly,
# mirroring how the openshorts suite ran them.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

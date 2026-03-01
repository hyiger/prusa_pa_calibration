"""Add project root to sys.path so tests can import _common, pa_cal, temp_tower."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

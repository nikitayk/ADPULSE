"""Shared pytest configuration for the ADPULSE test suite.

Adds the bidder source directory to ``sys.path`` so tests can import the
runtime modules (``Bid``, ``BidRequest``, ``data_source``) exactly the way
``app.py`` does, without packaging the project.
"""
import os
import sys

PYTHON_SRC = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "bidder.submission.code", "python")
)
if PYTHON_SRC not in sys.path:
    sys.path.insert(0, PYTHON_SRC)

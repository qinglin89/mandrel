"""Deployment tooling for the AI-native protocol suite."""

# Kept in step with pyproject's `version` by hand. Deriving it from installed
# metadata would be wrong here: the repo-local wrapper runs from PYTHONPATH with
# no install, and that is a supported mode.
__version__ = "2.3.0"

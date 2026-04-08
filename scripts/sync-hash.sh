#!/bin/bash
# Computes a deterministic state hash for ai-sync tracking.
# Used by both ai-sync (write marker) and hooks (read/compare).
# Single source of truth — do not duplicate this logic.
#
# State = HEAD commit SHA + uncommitted code file changes.
# Catches: multiple commits without sync, commit + new diff, diff only.
HEAD=$(git rev-parse HEAD 2>/dev/null || echo 'none')
DIFF=$(git diff HEAD -- ':(glob)**/*.go' ':(glob)**/*.proto' ':(glob)**/*.yaml' 2>/dev/null)
echo "${HEAD}:${DIFF}" | shasum | cut -d' ' -f1

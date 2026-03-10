# Order Retry Mechanism

Date: 2026-03

Problem

Exchange sometimes returns temporary network errors.

Decision

Add exponential backoff retry.

Implementation

execution/retry.go

"""Application layer: use-case orchestration.

Owns transaction boundaries, permission enforcement and audit emission. Depends on the
domain and on persistence; never on the interface.
"""

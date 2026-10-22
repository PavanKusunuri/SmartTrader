"""Read-only HTTP API for the local paper-trading dashboard.

PAPER TRADING — NO REAL ORDERS. This package exposes GET endpoints over the
SQLite ledger and the CSV data provider. It contains no order-placement code
path of any kind, and every database connection it opens is read-only.
"""

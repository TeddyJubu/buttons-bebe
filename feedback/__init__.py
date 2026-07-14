"""feedback — the Buttons Bebe learning loop (capture -> review -> promote).

Read-only against Gorgias. The ONLY writes are local KB files under kb/learned/
(a holding pen that is NOT indexed) and, after a human approves, kb/tickets/.

Nothing here ever messages a customer or writes to any external system.
"""
__all__ = ["config", "collector", "pairing", "pii", "similarity", "store"]

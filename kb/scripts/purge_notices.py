"""purge_notices.py -- physically drop expired Notice Board entries.

Run by the `buttonsbebe-kb-notices-gc` timer every 15 minutes. Expiry is already
enforced the instant a notice is read (see notices_lib.active_notices), so this
is only housekeeping to keep the stored board -- and the console's list -- tidy.
Uses the standard library only, so it needs no virtualenv.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from notices_lib import purge_expired

if __name__ == "__main__":
    removed = purge_expired()
    print(f"purged {removed} expired notice(s)")

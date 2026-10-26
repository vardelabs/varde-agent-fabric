"""The dispatcher's view of the adapter registry: forwarded variables, role checks, and limit detection."""
import sys

from dispatch.adapters import main

raise SystemExit(main(sys.argv[1:]))

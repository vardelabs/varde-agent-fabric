"""Print the caller variables a backend's registration forwards, one per line."""
import sys

from dispatch.adapters import main

raise SystemExit(main(sys.argv[1:]))

"""Command-line surface.

Kept import-light on purpose. `voxvault --help` and `voxvault doctor` must feel
instant, which means no module here may import numpy or the inference runtime
at import time -- only inside the handler that actually needs it.
"""

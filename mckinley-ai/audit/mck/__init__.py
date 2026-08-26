"""McKinley AI — Phase 0 data audit toolkit.

Read-only tooling that inspects an existing Lightroom / Aftershoot archive and
answers one question: is there enough signal in the historical data to train a
personalized culling model?

Nothing in this package writes to, moves, or modifies source photographs or
their sidecars. The only files it creates are reports and snapshots, written to
an output directory you choose.
"""

__version__ = "0.1.0"

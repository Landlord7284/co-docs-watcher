"""The source adapter: everything that knows what RAD/ENETWeb looks like on the wire.

Nothing outside this package imports from here — the pipeline depends on the ``Source``
protocol and on the neutral models, and ``run.py`` is the single, allowlisted exception
that builds the adapter. An architecture test enforces the seam on every CI run.

Wire-format names (``temErro``, ``SolicitarCaptcha``, ``numSequencia``, ``numVersao``,
``numProtocolo``) are quoted literally inside this package and nowhere else.
"""

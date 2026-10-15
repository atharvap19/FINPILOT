"""The query agent and the financial-report writer.

Both are the same loop (:mod:`app.agent.runner`): Gemini is given a goal and a
set of tools (:mod:`app.agent.tools`) over one job -- the ledger, the segments,
the bills and the vector index of the uploaded documents -- and decides for
itself which to call, in what order, and when it knows enough to answer.

The ledger stays the source of truth. Tools compute every figure with code;
the model chooses what to look at and writes the prose.
"""

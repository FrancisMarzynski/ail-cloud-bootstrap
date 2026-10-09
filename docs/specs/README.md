# Specs

One file per change: `<ID>-<slug>.md`, written by `/spec` after a `/grill`, and approved by Francis before `/ship` builds it. The top section is a brief for humans. Everything below it is the builder's and reviewer's contract. Short-lane owners write behavioural tests; full-lane independent writers lock them in `tests/acceptance/`. Both lanes protect the approved contract, use independent local review and allow one consolidated repair followed by focused confirmation.

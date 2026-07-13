"""Analysis pillar — measurement, not simulation.

`services` answer "run the simulation"; `analysis` answers "explain exactly what
happened" and "how does it compare to reality". The canonical object is
PossessionAccounting (accounting.py): every diagnostic, calibration report, and
historical comparison consumes it rather than recomputing possession math.
"""

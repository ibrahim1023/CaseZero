# packages/investigation

The stage machine: claims → ≥3 competing hypotheses → support search →
falsification (critic) → confidence revision → causal graph → final
assessment → LOCK. Pydantic AI agents with typed results inside deterministic
stage runners; Postgres checkpoints make stages resumable; every mutation
appends to the investigation event log (replay substrate).

No web or Context.dev capability exists in this layer — blindness is
capability absence plus RLS, not prompts (ADR 0007). Falsification design:
ADR 0005.

Status: planned (Phases 3–5; gets its own spec first).

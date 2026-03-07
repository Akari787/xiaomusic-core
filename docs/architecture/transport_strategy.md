# Transport Strategy

## Principles

- Router computes candidate transports using `TransportCapabilityMatrix[action] ∩ TransportPolicy[action]`.
- Router executes candidates in policy order and falls back only inside candidate set.
- Transport implementations execute protocol actions only.

## Current Policy

- `play`: `mina`
- `tts`: `miio`, fallback `mina`
- `volume`: `miio`, fallback `mina`
- `stop`: `miio`, fallback `mina`
- `pause`: `miio`, fallback `mina`
- `probe`: `miio`, fallback `mina`

## Miio play_url Final Decision

- `MiioTransport.play_url` is explicitly unsupported.
- Play capability matrix excludes Miio (`play=["mina"]`).
- This avoids ambiguous placeholder behavior and keeps play routing deterministic.

## Diagnostic Logging

- Router logs: `candidate_transports`, `selected_transport`, `fallback_triggered`.
- Transport logs: `action`, `latency_ms`, `success`.

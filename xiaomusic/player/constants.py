"""Timing constants shared by the device player modules."""

# Send the next track this long before the current one ends. The speaker
# restarts a finished single-item queue, so a late switch is audible as the
# old track starting over. Goes away with the speaker-owned queue (ADR-0007).
TRACK_END_LEAD_SEC = 2.0
# Queue dispatch (ADR-0007 step 3): the speaker switches tracks itself and the
# duration timer is only a fallback for a missed fetch. The next fetch lands
# at the track boundary (live-paced sources about 4 s later), so the fallback
# waits this long past the expected end.
QUEUE_FOLLOW_GRACE_SEC = 8.0
# A dispatch through the StreamGateway counts as started when the speaker
# fetches the start item (about 1 s after the cloud call returns on the
# OH2P). No fetch within this time is a failed start.
START_FETCH_TIMEOUT_SEC = 15.0

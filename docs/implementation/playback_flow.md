# Unified Playback Flow

/api/v1/play
  -> resolve adapter
  -> get PlayResolution
  -> build unified context
  -> send to device_player (ONLY path)

Forbidden:
- play_url direct call
- multi-path execution

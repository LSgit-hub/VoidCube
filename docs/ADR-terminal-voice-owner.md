# ADR: Terminal Voice Owner

## Status

Accepted on 2026-07-31. Terminal recording and TTS now use the explicit async adapter;
the CLI reports them unavailable until the canonical voice requirements are met.

## Context

The terminal CLI contains a legacy synchronous voice flow with recorder state, TTS
flags, playback interruption hooks, and a `/voice tts` command. Its actual TTS
methods are deliberately disabled, while `tools.voice_mode` retained an
independent facade with its own module-global `AudioPlayer` and event-loop bridge.

`systems.voice` already owns the current asynchronous device and transport model:
`VoiceConfig`, `AudioRecorder`, `AudioPlayer`, `SpeechToText`, `TextToSpeech`,
and `VoiceSessionManager`. Its recorder and playback lifecycle do not implement
the legacy CLI recorder contract, so wrapping it to mimic `start`, `stop`,
`cancel`, or `shutdown` would make an obsolete API a new main path.

This produced contradictory product state: the CLI could show TTS as enabled even
though it did not speak, and cancellation could target a player unrelated to a
real `VoiceSessionManager`.

## Decision

- `systems.voice` is the sole canonical owner of microphone capture, device
  playback, STT/TTS configuration, temporary audio lifecycle, and interruption.
- `VoidCube_cli` is a terminal adapter. It may own slash-command parsing,
  prompt-toolkit display, terminal status projection, and mapping a user response
  to a future `systems.voice` call; it must not own a second audio player or a
  transport compatibility contract.
- `/voice tts` is not a supported enabled feature until the CLI has an explicit
  asynchronous adapter to the canonical voice session. The UI and status output
  must describe it as unavailable rather than enabled.
- `tools.voice_mode` was not a canonical transport owner and has been removed
  after its terminal callers migrated. No replacement synchronous recorder
  facade may be added.
- The future adapter must use explicit operations for speak, interrupt, status,
  and continuous-session control. It must not pass `VoidcubeCLI` or a complete
  `VoiceSessionManager` through a generic host object.

## Consequences

The CLI terminal adapter owns only display state and input mapping. The canonical
manager owns capture, transcription, playback, interruption, and temporary audio
cleanup; the adapter does not expose a recorder-shaped compatibility API.

No model provider, authentication flow, request protocol, skill, or packaging
contract changes are authorized by this ADR.

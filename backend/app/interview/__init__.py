"""Adaptive panel interview (plan-v3.md).

Agora carries the media (candidate camera/mic in a real WebRTC channel, participant
model, active-speaker, resilience); Gemini Live is the brain (native barge-in). The
AI's audio is relayed over this app's own WebSocket and republished into the same
Agora channel from the browser as a bot UID — see plan-v3.md §5.1 for why the bot
audio is published browser-side rather than from a server-side Agora SDK.
"""

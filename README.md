# HA Music

Home Assistant add-on for radio and future Apple Music support.

## Status

Early development scaffold on `main`. **No release published.**

- Radio interface and configurable presets
- Apple Music tab visible, deliberately disabled ("In Vorbereitung")
- Playback architecture planned for Alexa Echo devices and existing Alexa multiroom groups
- Per-room volume/mute is not active until the Alexa integration capabilities are verified
- No Apple Music login or family account access is implemented

## Installation (development only)

Add `https://github.com/criticallimit/HA-Music` as an add-on repository in Home Assistant.
No tagged release has been created. The Ingress interface is a development preview.

## Roadmap

1. Validate Alexa Devices text commands, per-device volume services, and group playback in Home Assistant.
2. Implement radio directory, station selection, playback and metadata (title + cover).
3. Implement room controls with stored pre-mute volume and explicit availability/error handling.
4. Add native Lovelace card, reusing the same frontend modules as Ingress (not an iframe).
5. Add Apple Music family-account architecture with account-level authorization and playback capability checks.

## Design rules

Keep playback providers separate from station metadata. An Echo device in an Alexa group cannot be joined/removed from that group dynamically through Home Assistant; room toggles will represent muted/unmuted status, not group membership.

Development on `main` only. Publish a release only after explicit approval.

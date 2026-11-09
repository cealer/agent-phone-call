# Agent Call (Android app)

The phone side of agent-phone-call: rings full-screen when your agent calls (FCM data message), carries the
call over WebRTC on your private network, shows live subtitles, and lets you call your agents from the contact
list. It also keeps the notices and decisions a coordinator agent sends, and offers three home-screen widgets
(Decisions, Fleet, Talk).

Setup, build and configuration are in [SETUP.md](../SETUP.md) (steps 5, 8 and 9).

| Path | What it is |
|---|---|
| `lib/main.dart` | App start, push handling, the home screen and dialing. |
| `lib/call_screen.dart` | The live call: WebRTC, the call socket, speaker and mute. |
| `lib/call_teardown.dart` | Gives the phone back (microphone, audio mode, system call) however a call ends. |
| `lib/call_transcript.dart` | The live subtitles. |
| `lib/incoming.dart` | Push → full-screen incoming call, and the server's call endpoints. |
| `lib/notices.dart`, `lib/notice_inbox.dart`, `lib/notices_screen.dart` | Notices: notifications, their inbox on the phone, and its screens. |
| `lib/tasks.dart`, `lib/decisions.dart` | The coordinator's task snapshot and the Decisions section. |
| `lib/ui.dart` | Theme and the layouts (kept apart from plugins so they can be tested and rendered). |
| `android/app/src/main/kotlin/…` | The widgets (Fleet, Decisions, Talk), their activities, and the audio release on hang-up. |
| `tool/screenshots_test.dart` | Renders the README screenshots with made-up data. |

```sh
flutter analyze && flutter test
```

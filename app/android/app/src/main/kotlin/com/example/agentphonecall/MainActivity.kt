package com.example.agentphonecall

import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.telecom.CallAudioState
import com.cloudwebrtc.webrtc.audio.AudioSwitchManager
import com.hiennv.flutter_callkit_incoming.CallkitConnection
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var pendingCall: String? = null
    /** Decisions section to open: "" for the list, or the decision (id, or text without an id) tapped in the widget. */
    private var pendingOpenDecisions: String? = null

    override fun onCreate(savedInstanceState: android.os.Bundle?) {
        super.onCreate(savedInstanceState)
        pendingCall = intent?.getStringExtra(TalkWidget.EXTRA_CALL)
        pendingOpenDecisions = requestedDecision(intent)
        pinIfAsked(intent)
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra(TalkWidget.EXTRA_CALL)?.let { pendingCall = it }
        requestedDecision(intent)?.let { pendingOpenDecisions = it }
        pinIfAsked(intent)
    }

    private fun requestedDecision(intent: android.content.Intent?): String? =
        if (intent?.getBooleanExtra(DecisionsWidget.EXTRA_OPEN, false) == true)
            intent.getStringExtra(DecisionsWidget.EXTRA_DECISION) ?: ""
        else null

    /** `am start --es pin fleet|talk|decisions`: asks Android to pin that widget now, whether or not it was asked before. */
    private fun pinIfAsked(intent: android.content.Intent?) {
        val type = when (intent?.getStringExtra("pin")) {
            "fleet" -> FleetWidget::class.java
            "talk" -> TalkWidget::class.java
            "decisions" -> DecisionsWidget::class.java
            else -> return
        }
        val manager = android.appwidget.AppWidgetManager.getInstance(this)
        if (manager.isRequestPinAppWidgetSupported) manager.requestPinAppWidget(android.content.ComponentName(this, type), null, null)
    }

    /** If one of our widgets is on no screen, asks Android to pin it (once per widget; the user accepts). */
    private fun askForWidgetOnce(type: Class<*>, flag: String) {
        val prefs = getSharedPreferences(FleetWidget.PREFS, MODE_PRIVATE)
        val manager = android.appwidget.AppWidgetManager.getInstance(this)
        val component = android.content.ComponentName(this, type)
        if (prefs.getBoolean(flag, false) || manager.getAppWidgetIds(component).isNotEmpty()) return
        if (manager.isRequestPinAppWidgetSupported && manager.requestPinAppWidget(component, null, null)) {
            prefs.edit().putBoolean(flag, true).apply()
        }
    }

    /**
     * On hang-up, gives the phone's audio back to the other apps, whatever happened before:
     * 1. WebRTC's audio session (the call mode and audio focus it asked for): flutter_webrtc only lets go of it when the
     *    last PeerConnection is disposed, and a microphone opened after the hang-up keeps it taken with none left;
     * 2. this call's system call (Telecom), even if it is no longer in the plugin's list;
     * 3. behind both (stop() runs on the main thread), the normal audio mode, no forced speaker, no Bluetooth SCO and
     *    no pinned communication device. The mode is only touched if it was left in «internet call»: a real phone
     *    call (MODE_IN_CALL) is never touched.
     */
    private fun releaseAudio(callId: String?, result: MethodChannel.Result) {
        try { AudioSwitchManager.instance?.stop() } catch (_: Exception) {}
        try { callId?.let { CallkitConnection.find(it) }?.markEnded() } catch (_: Exception) {}
        Handler(Looper.getMainLooper()).post {
            try {
                val audio = getSystemService(AUDIO_SERVICE) as AudioManager
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) audio.clearCommunicationDevice()
                @Suppress("DEPRECATION")
                if (audio.isSpeakerphoneOn) audio.isSpeakerphoneOn = false
                @Suppress("DEPRECATION")
                if (audio.isBluetoothScoOn) { audio.stopBluetoothSco(); audio.isBluetoothScoOn = false }
                if (audio.mode == AudioManager.MODE_IN_COMMUNICATION) audio.mode = AudioManager.MODE_NORMAL
            } catch (_: Exception) {}
            result.success(true)
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // A call answered from the lock screen is a self-managed Telecom call: Android
        // routes its audio through the Connection and ignores app-level speaker changes.
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "phonecall/audio")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "setSpeaker" -> {
                        val id = call.argument<String>("callId")
                        val on = call.argument<Boolean>("on") ?: false
                        val connection = id?.let { CallkitConnection.find(it) }
                        if (connection == null) {
                            result.success(false) // not a Telecom call: caller uses WebRTC routing
                        } else {
                            @Suppress("DEPRECATION")
                            connection.setAudioRoute(
                                if (on) CallAudioState.ROUTE_SPEAKER else CallAudioState.ROUTE_WIRED_OR_EARPIECE
                            )
                            result.success(true)
                        }
                    }
                    "release" -> releaseAudio(call.argument<String>("callId"), result)
                    else -> result.notImplemented()
                }
            }
        // The «Fleet» widget needs the server URL and token: the app hands them over when it opens.
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "phonecall/widget")
            .setMethodCallHandler { call, result ->
                if (call.method == "setConfig") {
                    val url = call.argument<String>("url"); val token = call.argument<String>("token")
                    // Empty ones (an APK without --dart-define) do not overwrite the good configuration the widgets have.
                    if (!url.isNullOrBlank() && !token.isNullOrBlank())
                        getSharedPreferences(FleetWidget.PREFS, MODE_PRIVATE).edit()
                            .putString("url", url).putString("token", token).apply()
                    sendBroadcast(android.content.Intent(this, FleetWidget::class.java).setAction(FleetWidget.ACTION_REFRESH))
                    askForWidgetOnce(FleetWidget::class.java, "asked2")
                    result.success(true)
                } else if (call.method == "takeOpenDecisions") {
                    result.success(pendingOpenDecisions)
                    pendingOpenDecisions = null
                } else if (call.method == "takeDial") {
                    result.success(pendingCall)
                    pendingCall = null
                } else result.notImplemented()
            }
        // «Decisions» section: the state and the sending are the same the widget uses (DecisionStore / DecisionSender).
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "phonecall/decisions")
            .setMethodCallHandler { call, result ->
                val app = applicationContext
                fun inBackground(work: () -> Any?) = Thread {
                    val output = try { work() } catch (e: Exception) { "Error: ${e.message}" }
                    runOnUiThread { result.success(output) }
                }.start()
                when (call.method) {
                    "states" -> result.success(DecisionStore.all(app).toString())
                    "answer" -> {
                        val id = call.argument<String>("id"); val option = call.argument<String>("option"); val title = call.argument<String>("title") ?: ""
                        if (id == null || option == null) result.error("args", "missing id or option", null)
                        else inBackground { DecisionSender.answer(app, id, title, option) }
                    }
                    "callMe" -> {
                        val id = call.argument<String>("id"); val title = call.argument<String>("title") ?: ""
                        if (id == null) result.error("args", "missing id", null) else inBackground { DecisionSender.callMe(app, id, title) }
                    }
                    else -> result.notImplemented()
                }
            }
    }
}

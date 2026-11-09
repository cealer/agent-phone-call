import java.util.Base64

plugins {
    id("com.android.application")
    id("com.google.gms.google-services")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.example.agentphonecall"
    compileSdk { version = release(37) { minorApiLevel = 0 } }
    ndkVersion = flutter.ndkVersion

    compileOptions {
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.example.agentphonecall"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = 26
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
            // R8 renamed kotlin.coroutines.Continuation in shared_preferences_android and the app failed with
            // NoSuchMethodError (notice inbox, home screen and background notices). Without minifying there is no such risk.
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }
}

// A release APK without SERVER_URL and PHONE_TOKEN never reaches the Mac: Fleet «offline», no contacts, no registration.
// flutter build passes the --dart-define values base64-encoded in the dart-defines property.
gradle.taskGraph.whenReady {
    if (allTasks.none { it.project == project && it.name == "assembleRelease" }) return@whenReady
    val defines = (project.findProperty("dart-defines") as String?).orEmpty().split(",").filter { it.isNotBlank() }
        .map { String(Base64.getDecoder().decode(it)) }
    for (key in listOf("SERVER_URL", "PHONE_TOKEN")) {
        if (defines.none { it.startsWith("$key=") && it.length > key.length + 1 })
            throw GradleException("Missing --dart-define=$key=... (the app could not talk to the Mac)")
    }
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")
    testImplementation("junit:junit:4.13.2")
    // The SDK's org.json is a stub in local unit tests; this is the real implementation.
    testImplementation("org.json:json:20240303")
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}

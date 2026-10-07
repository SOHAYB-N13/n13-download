plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.ksp)
}

// ---------------------------------------------------------------------------
// Release signing
// ---------------------------------------------------------------------------
// The keystore and its credentials live OUTSIDE this repository so the private
// key can never be committed. Gradle reads them from the user-level
// ~/.gradle/gradle.properties, falling back to environment variables so CI can
// inject the same values without a file:
//
//   N13_KEYSTORE_FILE, N13_KEYSTORE_PASSWORD, N13_KEY_ALIAS, N13_KEY_PASSWORD
//
// A fresh clone still builds debug normally; the release build fails with a
// clear message rather than quietly producing an unsigned APK. See SIGNING.md.
fun signingValue(name: String): Provider<String> =
    providers.gradleProperty(name).orElse(providers.environmentVariable(name))

val keystoreFile: Provider<String> = signingValue("N13_KEYSTORE_FILE")
val keystorePassword: Provider<String> = signingValue("N13_KEYSTORE_PASSWORD")
val keyAliasName: Provider<String> = signingValue("N13_KEY_ALIAS")
val keyPasswordValue: Provider<String> = signingValue("N13_KEY_PASSWORD")

val releaseSigningConfigured: Boolean = listOf(
    keystoreFile,
    keystorePassword,
    keyAliasName,
    keyPasswordValue,
).all { it.orNull?.isNotBlank() == true }

android {
    namespace = "com.sohayb.n13download"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "com.sohayb.n13download"
        minSdk = 26
        targetSdk = 37
        versionCode = 4
        versionName = "1.1.2"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (releaseSigningConfigured) {
            create("release") {
                storeFile = file(keystoreFile.get())
                storePassword = keystorePassword.get()
                keyAlias = keyAliasName.get()
                keyPassword = keyPasswordValue.get()
            }
        }
    }

    buildTypes {
        release {
            // Only set when the key is actually available; otherwise the guard
            // task below stops the build instead of emitting an unsigned APK.
            if (releaseSigningConfigured) {
                signingConfig = signingConfigs.getByName("release")
            }
            // Code shrinking + resource optimization (R8).
            //
            // `packageScope` is deliberately left at its documented default of
            // "**".  It used to be narrowed to `androidx.**, kotlin.**,
            // kotlinx.**`, which excluded the app's own code and split the
            // program into two optimization domains — R8 then changed a Kotlin
            // stdlib class's visibility while a class outside that scope still
            // extended it, and every release build died at startup with
            // `IllegalAccessError` in androidx.startup.  Debug builds do not
            // shrink, so this only ever showed up in a release APK.
            optimization {
                enable = true
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
    }
}

// Refuse to produce a release artifact when signing is not configured.
//
// v1.1.0 and v1.1.1 were published with unsigned APKs because nothing caught it:
// the build succeeded and the file looked like a release. Failing here keeps that
// mistake from being made again, and touches only release tasks — debug builds
// are unaffected.
//
// The action captures a local Boolean rather than the script property, so the
// configuration cache can serialize it.
tasks.matching {
    it.name == "packageRelease" ||
        it.name == "packageReleaseBundle" ||
        it.name == "assembleRelease"
}.configureEach {
    val signingReady = releaseSigningConfigured
    doFirst {
        check(signingReady) {
            "Release signing is not configured, so the release build would " +
                "produce an UNSIGNED APK. Set N13_KEYSTORE_FILE, " +
                "N13_KEYSTORE_PASSWORD, N13_KEY_ALIAS and N13_KEY_PASSWORD in " +
                "~/.gradle/gradle.properties (or as environment variables). " +
                "See android/SIGNING.md."
        }
    }
}

ksp {
    arg("room.generateKotlin", "true")
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.service)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.kotlinx.coroutines.android)

    // Real HTTP/HTTPS transfer engine.
    implementation(libs.okhttp)

    // Persistent download state.
    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)

    // Settings.
    implementation(libs.androidx.datastore.preferences)

    testImplementation(libs.junit)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}

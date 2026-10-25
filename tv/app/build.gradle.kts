plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization")
}

// Everything deployment-specific comes from the build shell's environment. Nothing secret or
// owner-specific is ever written into the repo.
//   SUBFEED_API_BASE   https://<server IP or hostname> of your Caddy front (no trailing slash)
//   SUBFEED_API_TOKEN  the service's bearer token (baked into the APK)
//   SUBFEED_CA_PEM     path to the server's Caddy root CA (root.crt); the app trusts only this CA
//                      for the API host
//   SUBFEED_KEYSTORE / SUBFEED_KEYSTORE_PASS   release signing (assembleRelease only)
val apiBase: String = (System.getenv("SUBFEED_API_BASE") ?: "").trimEnd('/')
val apiToken: String = System.getenv("SUBFEED_API_TOKEN") ?: ""
val caPem: String? = System.getenv("SUBFEED_CA_PEM")?.takeIf { it.isNotBlank() }
// Host part of the base URL, for the network security config.
val apiHost: String = apiBase.removePrefix("https://").substringBefore('/').substringBefore(':')
val keystorePath: String? = System.getenv("SUBFEED_KEYSTORE")
val keystorePass: String? = System.getenv("SUBFEED_KEYSTORE_PASS")

android {
    namespace = "com.gerstl.subfeed"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.gerstl.subfeed"
        minSdk = 28
        targetSdk = 34
        versionCode = (System.getenv("SUBFEED_VERSION_CODE") ?: "1").toInt()
        versionName = "0.${versionCode}"
        // Unit tests and IDE syncs do not need a real server; release builds refuse to run without one.
        buildConfigField("String", "API_BASE", "\"${apiBase.ifBlank { "https://subfeed.invalid" }}\"")
        buildConfigField("String", "API_TOKEN", "\"$apiToken\"")
    }

    signingConfigs {
        create("release") {
            if (keystorePath != null && keystorePass != null) {
                storeFile = file(keystorePath)
                storePassword = keystorePass
                keyAlias = "subfeed"
                keyPassword = keystorePass
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("release")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    testOptions { unitTests.isReturnDefaultValues = true }
}

// Generated resources: the pinned CA (res/raw/subfeed_ca.pem, copied from SUBFEED_CA_PEM) and the
// network security config that trusts only that CA for the API host. Everything else (stream and
// thumbnail hosts) uses the system trust store. No cleartext anywhere, no trust-all path.
// Written at configuration time so every resource task sees the files without extra wiring.
val generatedRes = layout.buildDirectory.dir("generated/subfeed/res").get().asFile
run {
    val raw = File(generatedRes, "raw").apply { mkdirs() }
    val xml = File(generatedRes, "xml").apply { mkdirs() }
    val pem = File(raw, "subfeed_ca.pem")
    if (caPem != null && file(caPem).isFile) {
        file(caPem).copyTo(pem, overwrite = true)
    } else {
        // Placeholder so unit-test builds resolve resources; assembleRelease refuses to run
        // without the real CA (see below).
        pem.writeText("-----BEGIN CERTIFICATE-----\nplaceholder\n-----END CERTIFICATE-----\n")
    }
    val host = apiHost.ifBlank { "subfeed.invalid" }
    File(xml, "network_security_config.xml").writeText(
        """<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="false">
        <trust-anchors>
            <certificates src="system" />
        </trust-anchors>
    </base-config>
    <domain-config cleartextTrafficPermitted="false">
        <domain includeSubdomains="false">$host</domain>
        <trust-anchors>
            <certificates src="@raw/subfeed_ca" />
        </trust-anchors>
    </domain-config>
</network-security-config>
"""
    )
}
android.sourceSets.getByName("main").res.srcDir(generatedRes)

// Refuse to produce a release APK that cannot talk to the service.
tasks.matching { it.name == "assembleRelease" }.configureEach {
    doFirst {
        require(apiBase.startsWith("https://")) { "SUBFEED_API_BASE is not set (expected https://<your server>, e.g. https://192.0.2.10)" }
        require(caPem != null && file(caPem).isFile) { "SUBFEED_CA_PEM is not set or is not a file (path to the server's Caddy root.crt)" }
        require(apiToken.isNotBlank()) { "SUBFEED_API_TOKEN is not set in the build environment" }
        require(keystorePath != null && keystorePass != null) { "SUBFEED_KEYSTORE / SUBFEED_KEYSTORE_PASS not set" }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.03")
    implementation(composeBom)
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.animation:animation")
    implementation("androidx.tv:tv-material:1.0.0")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("io.coil-kt:coil-compose:2.7.0")

    val media3 = "1.4.1"
    implementation("androidx.media3:media3-exoplayer:$media3")
    implementation("androidx.media3:media3-ui:$media3")
    implementation("androidx.media3:media3-datasource:$media3")

    testImplementation("junit:junit:4.13.2")
}

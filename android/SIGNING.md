# Android release signing

Every published Android release **must** be signed with the project's release
key. `v1.1.0` and `v1.1.1` were published unsigned and could not be installed;
this document exists so that does not happen again.

## The key

| | |
|---|---|
| Keystore | `~/.n13-signing/n13-release.jks` (**outside this repository**) |
| Alias | `n13` |
| Type | PKCS12, RSA 4096 |
| Valid until | 2054-02-22 |
| Certificate SHA-256 | `65:AE:D8:80:F5:EF:F3:2D:AC:AE:F8:C0:DD:2C:2F:D0:9C:B7:B1:C1:98:5A:3E:66:3E:98:19:98:8A:15:0B:9C` |

> **This key must be preserved forever.** Android identifies an app by its
> signing certificate: a build signed with a different key cannot update an
> existing installation — users would have to uninstall and lose their data.
> Every future release (1.1.3, 1.2.0, 2.0.0 …) must use this same keystore.
>
> **Back it up somewhere safe and offline.** If it is lost, the app can never be
> updated in place again.

The private key and its passwords are deliberately **not** in this repository.

## Credentials

Stored in the user-level Gradle properties file, outside the repository:

```
~/.gradle/gradle.properties
```

```properties
N13_KEYSTORE_FILE=/absolute/path/to/n13-release.jks
N13_KEYSTORE_PASSWORD=…
N13_KEY_ALIAS=n13
N13_KEY_PASSWORD=…
```

Environment variables with the same names are used as a fallback, so CI can
inject the credentials without a file:

```
N13_KEYSTORE_FILE  N13_KEYSTORE_PASSWORD  N13_KEY_ALIAS  N13_KEY_PASSWORD
```

`android/.gitignore` also blocks `*.jks`, `*.keystore`, `*.p12`, `*.pfx`,
`keystore.properties` and `signing.properties` as a second line of defence.

## Building a release

```bash
cd android
./gradlew :app:assembleRelease
```

If signing is not configured the build **fails** with an explanatory message
rather than producing an unsigned APK. Debug builds are unaffected and need no
key.

## Verifying a release APK

```bash
apksigner verify --verbose app/build/outputs/apk/release/app-release.apk
```

The output must report `Verifies` and list the `n13` signer. A file named
`app-release-unsigned.apk` is the failure case this document exists to prevent —
never publish it.

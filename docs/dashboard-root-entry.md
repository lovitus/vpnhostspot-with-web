# Dashboard root-owned entry

APK input is pinned p4 `ae6f39ed82153b5edf6c797bc1a27e17ab5ee845` plus `patches/0005-web-admin.patch`, not the maintenance source tree.

## Device failure and correction

On the API 33 enforcing device, the previous signed build accepted TCP under root and delivered the socket via Binder. The app then received an AVC: `untrusted_app` read/write of `magisk` `tcp_socket` denied, followed by Binder DeadObject during delivery (captured in `/tmp/web-post-install.txt`, line 787). Same-process setuid probes and app-only emulator PFD tests had not reproduced the SELinux domain boundary. Root-to-app TCP FD delegation is therefore removed, not worked around by relaxing SELinux.

## Ownership and authentication

The existing librootkotlinx root service creates, marks, binds and accepts the fixed downstream IPv4 gateway:8080 entry. SO_MARK `0x00030063` precedes listen (AOSP local_network netId 99, explicitlySelected, protectedFromVpn), so SYN-ACK and accepted sockets use the local return path. Root verifies the address against the interface's single IPv4 address and **retains both ends of its relay**. No root TCP descriptor is passed to the app.

The app creates an ephemeral **127.0.0.1-only** TCP listener and a new SecureRandom 256-bit capability for each bind generation. Only its port and capability travel in the existing private root command. Root can connect only to the hardcoded loopback address; the external entry remains hardcoded 8080. HTTP cannot select a destination, port, mark, command or gateway. Root prefixes the internal byte stream with the capability; app reads exactly 32 bytes and uses MessageDigest.isEqual before HTTP parsing. The capability is neither logged nor included in public HTTP. Direct unauthenticated access to the private listener is closed without response and is bounded by the same app connection limits/deadline. Local malicious apps can still attempt bounded denial of service, as with any localhost port; they cannot select a relay target or obtain dashboard data without the capability.

DashboardSocket now wraps an ordinary **app-owned** Socket. The numeric downstream gateway used by the unchanged HTTP Host allowlist comes from the session, not from a forwarding header or caller bytes. The public dashboard remains a read-only, unauthenticated downstream endpoint. Only the private transport is authenticated.

## Bounds, backpressure and lifetime

Root uses MessageQueue FD readiness and nonblocking sockets throughout; it has no per-connection Binder transactions or blocked network worker threads. A global 16-slot semaphore bounds accepted relays across downstreams. Two 16 KiB buffers per relay provide backpressure; readiness is removed while the corresponding buffer is full. EOF drains buffered data before closing, including a half-closed request. An independent root-looper deadline closes both sockets **three seconds after accept**, including loopback connect, authentication and response writes. The app separately bounds all private sockets, including unauthenticated ones, to 16 per session and three seconds.

All root relay state and timeout callbacks are confined to the root looper. Close is idempotent, removes readiness/deadline callbacks, closes both descriptors and releases exactly one slot. Stop/Clean cancels RootFlow, closes all relays and the external listener. App shutdown closes the private listener and all accepted sockets, interrupting blocked accept/read/write. Worker invokeOnCompletion handles cancellation before start. No uninterruptible Binder delivery can stall cancellation or replacement. The existing root replacement mutex joins the old generation before binding the same interface. App generation identity guards remain intact.

No SELinux policy, iptables rule, BPF map, UID-wide VPN exemption or persistent routing state is added. VPN remains enabled. Local gateway:8080 access uses the same root entry and preserves the same Host validation. App/root process death is handled by socket closure and the existing root service lifecycle.

## API and validation

Uses public Android 29 APIs: Os socket/accept/connect/read/write/fcntlInt/shutdown, ParcelFileDescriptor.dup strictly inside root, MessageQueue FD readiness, and ordinary ServerSocket/Socket. Linux SO_MARK option 36 remains the existing documented AOSP compatibility dependency; a failed mark is not silently ignored.

CI replays the exact patch, compiles production Kotlin/Rust, executes JVM, Android 29 socket, browser and DNS/counter regressions. Android tests cover authenticated loopback byte framing, trusted numeric Host, rejection of private-address Host, response I/O and stop/deadline interruption. They do **not** claim cross-domain SELinux or VPN device coverage. Device acceptance separately requires enforcing SELinux with VPN on, numeric gateway homepage/status/debug, invalid Host, slow clients, FD stability, private authentication and local access. The old emulator Binder/PFD tests were removed because they tested the rejected architecture.

The baseline VendorDataTest compatibility fixes and compile-only hidden-API test stubs are retained. Signed-test remains master-only, with separate verified build/sign runners and existing environment approval. Stable release workflow stays disabled.

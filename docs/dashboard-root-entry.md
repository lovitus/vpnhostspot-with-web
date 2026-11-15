# Dashboard root-owned entry

The shipped source is p4 `ae6f39ed82153b5edf6c797bc1a27e17ab5ee845` plus `patches/0005-web-admin.patch`. The maintenance source tree is not the APK input.

## Ownership and transport

`WebListener` executes in the existing librootkotlinx root service, not in the Rust daemon. Root creates an IPv4 socket, sets SO_REUSEADDR and SO_MARK `0x00030063`, binds the verified downstream IPv4 address at fixed port 8080, listens and **accepts as root**. The mark precedes listen, covering SYN-ACK and accepted sockets. No arbitrary port, proxy destination, FD-to-mark command, shell command, firewall rule, routing rule or UID exemption is exposed. Root verifies that the supplied address is the interface's single IPv4 address.

Device evidence requires root accept as well as root socket creation: accepting after changing to the app UID completes the handshake but stalls data. Accepting first, then changing UID permits HTTP. Passing the listener alone is therefore insufficient.

The existing librootkotlinx 2.0.0-beta02 (`acee0071016e0cd9dc701a5a9245295481f5712d`) RootFlow uses a channel with no PFD undelivered cleanup. It transports only a resource-free ready boolean here. Accepted descriptors use a private **synchronous** Binder transaction instead: the app verifies calling UID 0, reads the PFD, attaches it to the owning generation before acknowledging and closes every rejected/late delivery. The endpoint is passed only as part of the root command, not registered as an exported service. There is no FD channel buffer and no byte relay. Binder owns the transaction's parcel resources; the app owns a successfully decoded PFD. Failure before ownership transfer closes it in finally.

`DashboardSocket` adapts public PFD input/output streams to the existing Socket-based HTTP parser. It does not invoke an imaginary Socket(FileDescriptor) constructor or use hidden APIs. The trusted numeric gateway is supplied separately for Host checking. The domain allowlist remains unchanged. Socket creation and accept stay under root UID, while all HTTP parsing, JSON and app data access remain in the unprivileged process.

Root retains a duplicate solely for shutdown, guarded by an idempotent close operation. A private completion Binder releases it promptly; an independent 3-second root deadline covers queued delivery and app stalls. The app has its own 3-second deadline and shuts down both TCP halves before closing the PFD, so a retained duplicate cannot keep blocked reads/writes alive. Completion cleanup is registered with Job.invokeOnCompletion, including cancellation before the worker starts. At most 16 accepted connections across all root entries are retained, additionally bounded by the existing per-session app limit of 16. The global bound limits FD and Binder work, not HTTP functionality.

## Lifecycle and external state

Starting a routing session starts its entry. Stop and Clean cancel the RootFlow and close app resources. Root removes its MessageQueue readiness registration, closes the listening FD and shuts down/closes each accepted FD. Deadline and completion races are idempotent. Interrupted bind/listen startup closes the socket. Binder failure cancels the entry and reports failure. App death closes its FD table and the existing root service lifecycle cancels commands; root-process death closes its descriptors. Reapply creates fresh generation-bound endpoints. Root serializes the brief replacement transition and cancels/joins the old command before binding the same interface again, preventing an asynchronous cancellation/rebind race. The generation registry is removed on command completion. No persistent kernel configuration is introduced, and no new Rust daemon state or routing cleanup command is needed.

## API and compatibility

Public Android 29 SDK signatures verify `Os.socket`, `Os.accept`, `Os.bind`, `Os.listen`, `Os.shutdown`, `Os.setsockoptInt`, `Os.setsockoptTimeval`, `ParcelFileDescriptor.dup`, its AutoClose streams and MessageQueue descriptor listeners. Source: platform/prebuilts/sdk, 29/public/api/android.txt. The app minimum SDK is 29. SO_MARK's Linux option number 36 is used because the SDK does not expose that constant. The mark has the same AOSP dependency already documented for `DAEMON_REPLY_MARK` in shared/model.rs and README: local netId 99, explicitlySelected and protectedFromVpn. Failure is explicit; no silently unmarked fallback is attempted.

This is still subject to ROM SELinux permitting Binder FD transfer and app I/O on root-created TCP sockets. A successful same-process setuid probe does not establish cross-process Binder/SELinux compatibility. The final installed APK must prove this on the target device.

## Validation

Local Python lifecycle checks test wiring only. GitHub Dashboard patched source reconstructs the exact pinned source, compiles/tests Kotlin and runs existing JVM/Chromium/DNS regressions. Its Android 29 emulator step executes DashboardSocketTest against the production adapter with real TCP/PFD streams, checking numeric Host, response writes, deadline/stop interruption despite a retained root-side duplicate and late rejection. This emulator test does not claim root UID, mark routing or Binder cross-process SELinux coverage.

Required device acceptance: VPN remains on, downstream numeric-IP homepage/status/debug succeed, local access remains available, invalid Host is rejected, stop/Clean removes 8080 and active exchanges, reapply restores access, and repeated reconnect/stop cycles do not grow app/root FD counts. Inspect socket ownership/mark and real SYN/SYN-ACK traffic. Signed test workflow remains master-only with the existing separate signer/environment gate; this branch adds only unsigned validation and does not access signing secrets.

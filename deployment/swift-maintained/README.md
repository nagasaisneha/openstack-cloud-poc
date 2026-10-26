# Maintained Swift runtime candidate for the existing instance

Planning and isolated compatibility evidence only. **No live service cutover is authorized or implemented by these files.** The existing Swift 2.33 installation, data, rings/builders, hash secrets and service configuration remain intact.

The verified Noble/Gazpacho UCA index lacks Swift server packages. The proposed minimum OS-change route keeps the same Ubuntu instance and uses an isolated Python 3.12 runtime for **Swift 2.35.5 at Epoxy, then 2.37.4 at Gazpacho**. Core services continue to use matching Ubuntu packages. This is an upstream-runtime integration proposal, **not a Canonical-supported Swift package upgrade or vendor-certified whole-cloud path**. It requires explicit acceptance of runtime maintenance ownership before upgrade approval. If a vendor-supported package-only stack is mandatory, this proposal does not close that support gate; investigate a separately approved same-instance OS/package route instead.

`epoxy-requirements.lock` and `requirements.lock` include matching Keystone middleware, pinned artifacts and the documented [security overlay](../constraints/README.md). No secrets or device paths are embedded. `verify_source.py --version 2.35.5` and `--version 2.37.4` verify official release signatures and compare every wheel Python module with the signed source. This is not a complete wheel-build attestation or verification of every transitive publisher signature.

`scripts/rehearse_swift_upgrade.py` exercises real account/container/object backend engines, object integrity, forward/reverse reads, restored file/xattr integrity and the maintained-version SSRF header filter. All state lives in new private temporary directories. It opens no service listener and uses no cloud credentials. Matching both hops does not establish live proxy/token integration, service/process compatibility, updater consistency, capacity, durability or whole-WSL recovery.

After source recovery and migration approval, the reviewed cutover must:

1. Build a new versioned environment under a protected runtime root using the target OS interpreter and hash lock; run dependency/signature checks. Do not overwrite the package environment or source runtime.
2. Preserve exact config, rings/builders, salts, filesystem UUID/mount and ownership. Swift needs xattrs; existing ext4 is not automatically reformatted. [Swift's deployment guide](https://docs.openstack.org/swift/2026.1/deployment_guide.html) recommends XFS and requires thorough testing of other filesystems.
3. Review every actual systemd unit and replace only its Swift executable with the versioned runtime, preserving service user, arguments, limits, mount ordering and protected config paths. Package hooks must not unexpectedly activate old entry points. No generic unreviewed unit template is installed here.
4. Quiesce/stop the owned service scope, check pending updates, create a matched storage/config recovery point, then switch the runtime and start dependencies in documented order. Complete account/container/object consistency and proxy health checks before allowing writes.
5. Replay real Keystone two-tenant PUT/GET/hash/metadata/anonymous and cross-project denials, owned cleanup, and a pre-backup object recovery test. Confirm actual processes use the intended interpreter/version.
6. Retain the old checkpoint. Reverse object-format reads in a small component test do not authorize live schema/runtime downgrades. Recover the matched pre-cutover state if acceptance fails.

The final cloud remains incomplete until all mandatory maintained-release workflows pass. Staging uses its separate Hibiscus/Kolla design and needs independent validation.

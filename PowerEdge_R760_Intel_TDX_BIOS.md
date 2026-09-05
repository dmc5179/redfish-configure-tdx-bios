# Dell PowerEdge R760 - Intel TDX BIOS Settings

Required BIOS configuration for Intel TDX with remote attestation on a Dell PowerEdge R760.

**Note:** Attribute names below are assumed to match the R660 (BIOS 2.7.x). If your R760 BIOS uses different attribute names, run the audit script and check for `[MISS]` results — those indicate attribute name mismatches.

## 1. Memory Settings

| Attribute | Required Value | Notes |
|-----------|---------------|-------|
| `NodeInterleave` | `Disabled` | NUMA node interleaving must be off |

## 2. Processor Settings

| Attribute | Required Value | Notes |
|-----------|---------------|-------|
| `ProcX2Apic` | `Enabled` | x2APIC mode required for TDX |

## 3. System Security Settings — TDX

| Attribute | Required Value | Notes |
|-----------|---------------|-------|
| `MemoryEncryption` | `MultipleKeys` | Enables TME-MT (prerequisite for TDX) |
| `GlbMemIntegrity` | `Disabled` | Global Memory Integrity (MK-TME integrity mode) must be off |
| `IntelSgx` | `On` | SGX must be enabled (TDX depends on SGX quoting infrastructure) |
| `IntelTxt` | `On` | Intel TXT (Trusted Execution Technology) |
| `EnableTdx` | `Enabled` | Intel TDX — only visible after TME-MT is active |
| `KeySplit` | `1` | TME-MT/TDX key split — requires TME-MT active |
| `EnableTdxSeamldr` | `Enabled` | TDX SEAM Loader — requires TME-MT active |

## 4. System Security Settings — SGX Attestation Registration

These settings are required for TDX **remote attestation** to work. Without them,
TDX VMs will run but cannot be cryptographically verified by a remote party.

| Attribute | Required Value | Notes |
|-----------|---------------|-------|
| `SgxAutoRegistrationAgent` | `Enabled` | BIOS runs the Multi-Package Registration Agent on boot to register the platform with Intel |
| `SgxPackageInfoInBandAccess` | `On` | Allows OS/containers to read platform provisioning data (enc_ppid, manifest) |
| `SgxFactoryReset` | `On` (one-time) | Triggers Initial Platform Establishment — re-generates keys and forces fresh registration. Only needed if platform was never registered. Resets to `Off` after boot. |

## 5. Initialization & Activation

1. TDX-dependent attributes (`EnableTdx`, `KeySplit`, `EnableTdxSeamldr`) are not
   visible until `MemoryEncryption=MultipleKeys` is applied and the server reboots.
   This means enabling TDX from scratch requires **two reboots**.

2. After enabling `SgxAutoRegistrationAgent`, the BIOS MPA will attempt to register
   the platform with Intel's Registration Service on the next boot. This requires
   outbound HTTPS connectivity during boot. After successful registration, the
   platform identity persists and internet access is no longer needed.

3. The SEAM loader may require a full power cycle (not just a warm reboot) to
   initialize. The remediate script uses `GracefulRebootWithPowerCycle`.

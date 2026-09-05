# Dell PowerEdge R660 - Intel TDX BIOS Settings

Required BIOS configuration for Intel TDX with remote attestation on a Dell PowerEdge R660.

Attribute names are from iDRAC BIOS firmware 2.7.x as exposed via Redfish API.

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

## 5. Attribute Name Mapping

Dell BIOS attribute names differ from the labels shown in the F2 Setup screen:

| F2 Setup Label | Redfish Attribute |
|---------------|-------------------|
| Node Interleaving | `NodeInterleave` |
| x2APIC Mode | `ProcX2Apic` |
| Memory Encryption | `MemoryEncryption` |
| Global Memory Integrity | `GlbMemIntegrity` |
| Intel SGX | `IntelSgx` |
| Intel TXT | `IntelTxt` |
| Intel TDX | `EnableTdx` |
| TME-MT/TDX Key Split | `KeySplit` |
| TDX Secure Arbitration Mode Loader | `EnableTdxSeamldr` |
| Enable/Disable SGX Auto MP Registration Agent | `SgxAutoRegistrationAgent` |
| SGX Package Info In-Band Access | `SgxPackageInfoInBandAccess` |
| SGX Factory Reset | `SgxFactoryReset` |

## 6. Attribute Dependencies

The BIOS registry enforces runtime dependencies between SGX attributes:

- **When `SgxFactoryReset` is `On`** (current value or pending):
  - `SgxAutoRegistrationAgent` becomes **read-only** (forced to `Disabled`)
  - `SgxPackageInfoInBandAccess` becomes **read-only** (forced to `Off`)

This means `SgxFactoryReset` and the registration settings **cannot be applied
in the same PATCH or the same reboot**. The remediate script handles this
automatically:

1. Apply `SgxFactoryReset=On` → reboot → factory reset completes, resets to `Off`
2. Re-run script → apply `SgxAutoRegistrationAgent=Enabled` + `SgxPackageInfoInBandAccess=On` → reboot
3. MPA registration happens during this second boot

Other dependencies:
- `SgxAutoRegistrationAgent` is hidden when `IntelSgx=Off`
- `SgxPackageInfoInBandAccess` is hidden when `MemoryEncryption=Disabled`

## 7. Initialization & Activation

1. TDX-dependent attributes (`EnableTdx`, `KeySplit`, `EnableTdxSeamldr`) are not
   visible until `MemoryEncryption=MultipleKeys` is applied and the server reboots.
   This means enabling TDX from scratch requires **two reboots**.

2. SGX Factory Reset + registration requires an additional reboot cycle (see
   section 6). From a clean start, this means up to **three reboots** total.

3. After enabling `SgxAutoRegistrationAgent`, the BIOS MPA will attempt to register
   the platform with Intel's Registration Service on the next boot. This requires
   outbound HTTPS connectivity during boot. After successful registration, the
   platform identity persists and internet access is no longer needed.

4. The SEAM loader may require a full power cycle (not just a warm reboot) to
   initialize. The remediate script uses `PowerCycle` reset type.

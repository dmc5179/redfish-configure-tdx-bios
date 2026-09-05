# Intel TDX BIOS Configuration Scripts

Scripts for auditing and remediating Intel TDX BIOS settings on Dell PowerEdge servers via iDRAC Redfish API. Covers both TDX enablement and SGX attestation registration — the latter is required for TDX remote attestation to work end-to-end.

## Hardware Prerequisites

Before running these scripts, review **[PREREQUISITES.md](PREREQUISITES.md)** for:
- **SGX Platform Registration** — required for TDX remote attestation. Two options for disconnected environments: register before air-gapping (recommended), or use in-band registration via USB sneakernet.
- **BIOS configuration order** — settings have multi-reboot dependencies.
- **When to perform these steps** — should be done before OpenShift installation.

## Supported Platforms

| Model | Audit | Remediate | Verified |
|-------|-------|-----------|----------|
| Dell PowerEdge R660 | `r660_tdx_audit.py` | `r660_tdx_remediate.py` | BIOS 2.7.5, iDRAC 7.10.30.05 |
| Dell PowerEdge R760 | `r760_tdx_audit.py` | `r760_tdx_remediate.py` | Not yet verified — attribute names may differ |

## Prerequisites

```
pip install requests urllib3
```

## Authentication

All scripts accept iDRAC credentials via CLI flags or environment variables:

```bash
# CLI flags
./r660_tdx_audit.py --host 10.0.0.1 --username root --password secret

# Environment variables
export IDRAC_HOST=10.0.0.1
export IDRAC_USER=root
export IDRAC_PASSWORD=secret
./r660_tdx_audit.py

# Mix — CLI flags take precedence over env vars
export IDRAC_HOST=10.0.0.1
./r660_tdx_audit.py --password secret
```

| Flag | Env var | Default |
|------|---------|---------|
| `--host` / `-H` | `IDRAC_HOST` | (required) |
| `--username` / `-u` | `IDRAC_USER` | `root` |
| `--password` / `-p` | `IDRAC_PASSWORD` | (required) |

## What Gets Checked

The audit/remediate scripts check three categories of BIOS settings:

### 1. Prerequisite settings
Memory, processor, and security settings that must be active before TDX can be enabled: `NodeInterleave`, `ProcX2Apic`, `MemoryEncryption`, `GlbMemIntegrity`, `IntelSgx`, `IntelTxt`.

### 2. TDX-dependent settings
Settings that require TME-MT (`MemoryEncryption=MultipleKeys`) to be active before they become visible in the BIOS: `EnableTdx`, `KeySplit`, `EnableTdxSeamldr`.

### 3. SGX attestation registration
Settings required for TDX **remote attestation** — without these, TDX VMs run but cannot be cryptographically verified: `SgxAutoRegistrationAgent`, `SgxPackageInfoInBandAccess`.

## Audit Scripts

Check current BIOS settings against TDX + attestation requirements:

```
./r660_tdx_audit.py --host 10.0.0.1 --password secret
```

The reboot analysis tells you how many reboots are needed:

| Scenario | Reboots |
|----------|---------|
| All settings already correct | 0 |
| Only prerequisite OR only TDX-dependent changes needed | 1 |
| Both prerequisite AND TDX-dependent changes needed | 2 |
| Only SGX registration changes needed | 1 |
| SGX factory reset + registration changes needed | 2 (factory reset must complete before registration attrs are writable) |
| Full clean start (prerequisites + TDX + factory reset + registration) | 3 |

JSON output for automation:

```
./r660_tdx_audit.py --host 10.0.0.1 --password secret --json
```

The audit script exits 0 when all settings pass, 1 when changes are needed.

## Remediate Scripts

Apply required BIOS changes and trigger a reboot:

```
./r660_tdx_remediate.py --host 10.0.0.1 --password secret
```

Options:

| Flag | Purpose |
|------|---------|
| `--dry-run` | Show what would be changed without applying |
| `--sgx-factory-reset` | Also set `SgxFactoryReset=On` to trigger Initial Platform Establishment (needed if platform was never registered with Intel) |

The script handles multi-step dependencies automatically. Just re-run after each reboot until it reports "No changes needed":

1. **First run** — applies prerequisite settings and `SgxFactoryReset=On`, triggers reboot.
2. **Second run** — detects factory reset completed. If TDX-dependent settings are now visible, applies those. Applies SGX registration settings (now writable since factory reset is `Off`). Triggers reboot.
3. **Third run** (if needed) — applies any remaining TDX-dependent or registration settings.

### SGX Factory Reset dependency

The BIOS registry enforces a dependency: when `SgxFactoryReset` is `On` (even just pending), `SgxAutoRegistrationAgent` and `SgxPackageInfoInBandAccess` become **read-only**. The script detects this and defers registration settings to the next run after the factory reset reboot completes.

## iDRAC Firmware Compatibility

The remediate scripts auto-detect the correct Redfish endpoint for creating BIOS config jobs:

| iDRAC firmware | Job creation method |
|---------------|-------------------|
| Older (has `JobService.CreateJobAndReboot`) | Single POST to `JobService/Actions/JobService.CreateJobAndReboot` |
| Newer / 7.x (Dell OEM) | Two-step: POST to `Oem/Dell/Jobs` to create config job, then `ComputerSystem.Reset` with `PowerCycle` |

The scripts try the legacy endpoint first; if it returns 404, they automatically fall back to the Dell OEM flow. No manual configuration needed.

Shared logic lives in `idrac_common.py`, which both remediate scripts import.

## SGX Attestation Registration

TDX remote attestation requires the platform to be registered with Intel's Registration Service. Two BIOS settings control this:

- **`SgxAutoRegistrationAgent`** — When `Enabled`, the BIOS Multi-Package Registration Agent (MPA) registers the platform with Intel on boot. This is a one-time operation; after successful registration, the platform identity persists.

- **`SgxPackageInfoInBandAccess`** — When `On`, allows the OS and containers to read platform provisioning data (encrypted PPID, platform manifest) needed for PCK certificate retrieval.

If the platform has never been registered (e.g., Intel PCS returns 404 for PCK cert requests), you may also need `--sgx-factory-reset` to trigger Initial Platform Establishment:

```
./r660_tdx_remediate.py --host 10.0.0.1 --password secret --sgx-factory-reset
```

### Disconnected / Air-Gapped Environments

The BIOS MPA registers directly with Intel's cloud Registration Service, which won't work in a disconnected environment. Two options:

1. **Option A (Recommended): Register before air-gapping.** Enable `SgxAutoRegistrationAgent` and reboot the server while it still has outbound HTTPS access. After the one-time registration, the platform identity persists — internet is no longer needed.

2. **Option B: In-band registration via USB.** With `SgxPackageInfoInBandAccess=On`, extract platform provisioning data using `PCKIDRetrievalTool`, transport it to an internet-connected machine via USB, register with Intel, and bring collateral back.

After registration (either option), all attestation collateral must be loaded into a PCCS running in the disconnected enclave.

See **[PREREQUISITES.md](PREREQUISITES.md)** for detailed steps for both options, including the full sneakernet workflow.

## Reboot Impact

Each reboot causes full OS downtime. If the server is an OpenShift node:

1. Cordon and drain the node before running the remediate script
2. After the final reboot, uncordon the node

The SEAM loader may require a full power cycle (not just a warm reboot) to initialize. The remediate scripts use `PowerCycle` reset type.

## Required BIOS Settings

See `PowerEdge_R660_Intel_TDX_BIOS.md` and `PowerEdge_R760_Intel_TDX_BIOS.md` for the full list of required settings and their values.

# Intel TDX BIOS Configuration Scripts

Scripts for auditing and remediating Intel TDX BIOS settings on Dell PowerEdge servers via iDRAC Redfish API. Covers both TDX enablement and SGX attestation registration — the latter is required for TDX remote attestation to work end-to-end.

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

When both prerequisite and TDX-dependent settings need changes, the script handles this automatically:

1. **First run** — applies only prerequisite settings (+ registration settings) and triggers a reboot.
2. **Second run** — detects prerequisites are satisfied, applies TDX-dependent settings, and triggers a final reboot.

This two-phase approach is necessary because TDX BIOS attributes are not visible until TME-MT is active.

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

The BIOS MPA registers directly with Intel's cloud service, which won't work in a disconnected environment. For air-gapped deployments:

1. **Pre-deployment (internet-connected):** Enable `SgxAutoRegistrationAgent` and reboot the server while it still has outbound HTTPS access. After the one-time registration, the platform identity persists — internet is no longer needed.

2. **Or use in-band registration:** With `SgxPackageInfoInBandAccess=On`, the OS can read the platform's encrypted PPID and platform manifest via EFI variables. This data can be manually transported to an internet-connected machine, registered with Intel PCS, and the resulting PCK certificates loaded into a local PCCS.

3. **PCCS as local cache:** After registration (by either method), fetch all collateral (PCK certs, TCB Info, QE Identity, CRLs) from Intel PCS on the internet-connected side, then load it into a PCCS running in the disconnected enclave via the `PUT /sgx/certification/v4/platformcollateral` API.

## Reboot Impact

Each reboot causes full OS downtime. If the server is an OpenShift node:

1. Cordon and drain the node before running the remediate script
2. After the final reboot, uncordon the node

The SEAM loader may require a full power cycle (not just a warm reboot) to initialize. The remediate scripts use `PowerCycle` reset type.

## Required BIOS Settings

See `PowerEdge_R660_Intel_TDX_BIOS.md` and `PowerEdge_R760_Intel_TDX_BIOS.md` for the full list of required settings and their values.

# Intel TDX BIOS Configuration Scripts

Scripts for auditing and remediating Intel TDX BIOS settings on Dell PowerEdge servers via iDRAC Redfish API.

## Supported Platforms

| Model | Audit | Remediate |
|-------|-------|-----------|
| Dell PowerEdge R660 | `r660_tdx_audit.py` | `r660_tdx_remediate.py` |
| Dell PowerEdge R760 | `r760_tdx_audit.py` | `r760_tdx_remediate.py` |

## Prerequisites

```
pip install requests urllib3
```

## Authentication

All scripts accept iDRAC credentials via CLI flags or environment variables:

```bash
# CLI flags
./r760_tdx_audit.py --host 10.0.0.1 --username root --password secret

# Environment variables
export IDRAC_HOST=10.0.0.1
export IDRAC_USER=root
export IDRAC_PASSWORD=secret
./r760_tdx_audit.py

# Mix — CLI flags take precedence over env vars
export IDRAC_HOST=10.0.0.1
./r760_tdx_audit.py --password secret
```

| Flag | Env var | Default |
|------|---------|---------|
| `--host` / `-H` | `IDRAC_HOST` | (required) |
| `--username` / `-u` | `IDRAC_USER` | `root` |
| `--password` / `-p` | `IDRAC_PASSWORD` | (required) |

## Audit Scripts

The audit scripts check current BIOS settings against TDX requirements and report which settings need changes. They also analyze reboot impact:

```
./r660_tdx_audit.py --host 10.0.0.1 --password secret
```

The audit report categorizes settings into two groups:

- **Prerequisite settings** — memory, processor, and security settings that must be active before TDX can be enabled (NodeInterleaving, X2ApicMode, CpuPhysicalAddressLimit, MemoryEncryption, GlobalMemoryIntegrity, IntelSgx, IntelTxt)
- **TDX-dependent settings** — settings that require TME-MT (MemoryEncryption=MultipleKeys) to be active before they become visible in the BIOS (IntelTdx, TmeMtTdxKeySplit, TdxSeamLoader)

The reboot analysis tells you how many reboots are needed:

| Scenario | Reboots |
|----------|---------|
| All settings already correct | 0 |
| Only prerequisite OR only TDX-dependent changes needed | 1 |
| Both prerequisite AND TDX-dependent changes needed | 2 |

The audit script exits 0 when all settings pass, 1 when changes are needed.

## Remediate Scripts

The remediate scripts apply the required BIOS changes and trigger a reboot:

```
./r660_tdx_remediate.py --host 10.0.0.1 --password secret
```

When both prerequisite and TDX-dependent settings need changes, the script handles this automatically:

1. **First run** — applies only prerequisite settings and triggers a reboot. Prints a message telling you to re-run after the server comes back up.
2. **Second run** — detects that prerequisites are now satisfied, applies TDX-dependent settings, and triggers a final reboot.

This two-phase approach is necessary because the TDX BIOS attributes (IntelTdx, TdxSeamLoader, TmeMtTdxKeySplit) are not visible in the BIOS until TME-MT is active.

## Reboot Impact

Each reboot causes full OS downtime. If the server is an OpenShift node:

1. Cordon and drain the node before running the remediate script
2. After the final reboot, uncordon the node

The SEAM loader may require a full power cycle (not just a warm reboot) to initialize. The iDRAC jobs use `GracefulRebootWithPowerCycle` which should satisfy this requirement.

## Required BIOS Settings

See `PowerEdge_R660_Intel_TDX_BIOS.md` and `PowerEdge_R760_Intel_TDX_BIOS.md` for the full list of required settings and their values.

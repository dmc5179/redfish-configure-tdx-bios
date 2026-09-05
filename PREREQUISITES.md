# Hardware Prerequisites for Intel TDX Remote Attestation

This document covers the hardware-level prerequisites that must be completed
**before** deploying OpenShift Confidential Containers with TDX remote
attestation. These steps require specific network conditions and should be
planned early in the deployment process.

## 1. SGX Platform Registration with Intel

TDX remote attestation depends on the SGX quoting infrastructure. Before the
platform can generate verifiable TDX quotes, it must be **registered with
Intel's Registration Service**. This is a one-time operation that ties the
platform's hardware identity to Intel's PCK certificate infrastructure.

Without registration, Intel's Provisioning Certification Service (PCS) returns
HTTP 404 for PCK certificate requests, and the entire attestation chain —
QGS, PCCS, Trustee KBS — cannot function.

### Option A: Register Before Air-Gapping (Recommended)

If the server will eventually be deployed in a disconnected / air-gapped
environment, **perform SGX registration while the server still has outbound
internet access**. This is the simplest approach.

**Steps:**

1. Connect the server to a network with outbound HTTPS access to Intel's
   Registration Service (`https://api.trustedservices.intel.com`).

2. Configure the BIOS for TDX + SGX registration (see `PowerEdge_R660_Intel_TDX_BIOS.md`):
   - Enable all prerequisite settings (TME-MT, SGX, TXT, etc.)
   - Set `SgxFactoryReset=On` → reboot (re-generates platform keys)
   - Set `SgxAutoRegistrationAgent=Enabled` → reboot
   - The BIOS Multi-Package Registration Agent (MPA) registers the platform
     with Intel during POST. This is automatic and takes seconds.

3. Verify registration succeeded:
   ```bash
   # From any machine with internet access, query Intel PCS for the platform's PCK cert
   # You need the platform's encrypted PPID (from SgxPackageInfoInBandAccess or PCKIDRetrievalTool)
   curl -v "https://api.trustedservices.intel.com/sgx/certification/v4/pckcert?encrypted_ppid=<PPID>&pceid=<PCEID>&cpusvn=<CPUSVN>&pcesvn=<PCESVN>"
   # HTTP 200 = registered. HTTP 404 = not registered.
   ```

4. After successful registration, the platform identity persists in Intel's
   infrastructure permanently. The server can now be moved into the air-gapped
   environment. Internet access is no longer needed for attestation.

5. Before disconnecting: fetch all attestation collateral (PCK certificates,
   TCB Info, QE Identity, CRLs) from Intel PCS and load them into a PCCS
   instance running in the disconnected enclave.

**This is the approach used in this project.** The BIOS configuration scripts
in this repository handle steps 1-2 automatically.

### Option B: In-Band Registration via Sneakernet

If the server is **already in an air-gapped environment** and cannot be
temporarily connected to the internet, use the in-band registration method.

**Steps:**

1. Configure BIOS settings on the server:
   - Enable all prerequisite settings (TME-MT, SGX, TXT, etc.)
   - Set `SgxFactoryReset=On` → reboot
   - Set `SgxPackageInfoInBandAccess=On` → reboot
   - Do NOT enable `SgxAutoRegistrationAgent` (it would fail without internet)

2. Extract platform provisioning data from the server. The data is available
   via EFI variables or the `PCKIDRetrievalTool`:
   ```bash
   # On the bare metal host (or from a privileged container):
   PCKIDRetrievalTool -f platform_data.csv
   ```
   This produces a CSV containing the encrypted PPID, PCE ID, CPU SVN, PCE SVN,
   and QE ID for each CPU package.

3. Transport `platform_data.csv` to an internet-connected machine via USB drive
   or other sneakernet method.

4. On the internet-connected machine, register the platform and retrieve
   collateral:
   ```bash
   # Register with Intel's Registration Service
   # (Use the Multi-Package Registration tool or direct API calls)

   # Fetch PCK certificates
   curl "https://api.trustedservices.intel.com/sgx/certification/v4/pckcerts?encrypted_ppid=<PPID>&pceid=<PCEID>" \
     -H "Ocp-Apim-Subscription-Key: <YOUR_PCS_API_KEY>" \
     -o pckcerts.json

   # Fetch TCB Info, QE Identity, CRLs
   curl "https://api.trustedservices.intel.com/sgx/certification/v4/tcb?fmspc=<FMSPC>" -o tcbinfo.json
   curl "https://api.trustedservices.intel.com/sgx/certification/v4/qe/identity" -o qeidentity.json
   ```

5. Transport the collateral files back to the air-gapped environment via USB.

6. Load collateral into the PCCS running in the disconnected enclave:
   ```bash
   curl -k -X PUT "https://<PCCS_HOST>:8081/sgx/certification/v4/platformcollateral" \
     -H "Content-Type: application/json" \
     -H "user-token: <PCCS_ADMIN_TOKEN>" \
     -d @collateral.json
   ```

**Trade-offs:**
- More complex — requires manual data transfer steps
- Requires the `PCKIDRetrievalTool` to be available on the bare metal host
- Must be repeated if CPUs are replaced or SGX microcode is updated
- Does not require the server to ever have internet access

## 2. BIOS Configuration Order

The BIOS settings have dependencies that require multiple reboots. The
remediate scripts handle this automatically, but the sequence is:

| Step | Settings | Reboot? | Notes |
|------|----------|---------|-------|
| 1 | Prerequisites: `NodeInterleave`, `ProcX2Apic`, `MemoryEncryption`, `GlbMemIntegrity`, `IntelSgx`, `IntelTxt` | Yes | Enables TME-MT and SGX |
| 2 | TDX: `EnableTdx`, `KeySplit`, `EnableTdxSeamldr` | Yes | Only visible after step 1 reboot |
| 3 | Factory Reset: `SgxFactoryReset=On` | Yes | Re-generates platform keys |
| 4 | Registration: `SgxAutoRegistrationAgent=Enabled`, `SgxPackageInfoInBandAccess=On` | Yes | Read-only while `SgxFactoryReset=On` (step 3 must complete first) |

Steps 1 + 3 can be combined in a single reboot (the remediate script does
this). Steps 2 and 4 may also be combined if their respective prerequisites
are already met. Minimum reboots from a clean start: **3**.

## 3. When to Perform These Steps

These hardware prerequisites should be completed **before** installing
OpenShift on the server, or at minimum before deploying the CoCo operator
stack. The timing matters because:

- **MPA registration** (Option A) requires outbound internet access during
  boot, which may not be available after the cluster is configured with
  disconnected networking.
- **BIOS changes** require full server reboots. On a running OpenShift node,
  this means cordoning, draining, and uncordoning — disruptive to workloads.
- **Platform collateral** must be loaded into PCCS before any TDX workload
  can produce verifiable attestation quotes.

**Recommended order:**
1. Configure BIOS for TDX + SGX (this repo's scripts)
2. Complete SGX platform registration (Option A or B above)
3. Fetch and stage attestation collateral for the disconnected PCCS
4. Install OpenShift
5. Deploy CoCo operator stack via AutoShift
6. Load collateral into PCCS
7. Validate end-to-end attestation

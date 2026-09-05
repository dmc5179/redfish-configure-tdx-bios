#!/usr/bin/env python3
import argparse
import os
import requests
import urllib3
import sys

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Attribute names match Dell PowerEdge R660 iDRAC BIOS firmware 2.7.x
REQUIRED_SETTINGS = {
    "NodeInterleave": "Disabled",
    "ProcX2Apic": "Enabled",
    "MemoryEncryption": "MultipleKeys",
    "GlbMemIntegrity": "Disabled",
    "EnableTdx": "Enabled",
    "KeySplit": "1",
    "EnableTdxSeamldr": "Enabled",
    "IntelSgx": "On",
    "IntelTxt": "On",
}

PREREQUISITE_GROUP = {
    "NodeInterleave", "ProcX2Apic",
    "MemoryEncryption", "GlbMemIntegrity", "IntelSgx", "IntelTxt",
}

TDX_DEPENDENT_GROUP = {"EnableTdx", "KeySplit", "EnableTdxSeamldr"}

SGX_REGISTRATION_SETTINGS = {
    "SgxAutoRegistrationAgent": "Enabled",
    "SgxPackageInfoInBandAccess": "On",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Remediate Dell PowerEdge R660 BIOS settings for Intel TDX and SGX attestation"
    )
    parser.add_argument(
        "--host", "-H",
        default=os.environ.get("IDRAC_HOST"),
        help="iDRAC hostname or IP (env: IDRAC_HOST)",
    )
    parser.add_argument(
        "--username", "-u",
        default=os.environ.get("IDRAC_USER", "root"),
        help="iDRAC username (env: IDRAC_USER, default: root)",
    )
    parser.add_argument(
        "--password", "-p",
        default=os.environ.get("IDRAC_PASSWORD"),
        help="iDRAC password (env: IDRAC_PASSWORD)",
    )
    parser.add_argument(
        "--sgx-factory-reset",
        action="store_true",
        help="Also set SgxFactoryReset=On to trigger Initial Platform Establishment on next boot",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without applying",
    )
    args = parser.parse_args()
    if not args.host:
        parser.error("--host is required (or set IDRAC_HOST)")
    if not args.password:
        parser.error("--password is required (or set IDRAC_PASSWORD)")
    return args


def apply_tdx_settings(host, username, password, sgx_factory_reset=False, dry_run=False):
    base_url = f"https://{host}/redfish/v1/Systems/System.Embedded.1/Bios"
    settings_url = f"{base_url}/Settings"
    jobs_url = f"https://{host}/redfish/v1/Managers/iDRAC.Embedded.1/JobService/Actions/JobService.CreateJobAndReboot"

    print(f"Step 1: Connecting to R660 iDRAC at {host} to verify current config...")
    try:
        response = requests.get(base_url, auth=(username, password), verify=False, timeout=30)
        if response.status_code != 200:
            print(f"Error fetching BIOS attributes: {response.status_code}")
            sys.exit(1)
        current_settings = response.json().get("Attributes", {})
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    prerequisite_changes = {}
    tdx_dependent_changes = {}
    tdx_attrs_missing = []
    registration_changes = {}

    for setting, req_value in REQUIRED_SETTINGS.items():
        curr_value = current_settings.get(setting)
        if setting in TDX_DEPENDENT_GROUP:
            if curr_value is None:
                tdx_attrs_missing.append(setting)
            elif str(curr_value).strip().lower() != str(req_value).strip().lower():
                tdx_dependent_changes[setting] = req_value
        else:
            if curr_value is None or str(curr_value).strip().lower() != str(req_value).strip().lower():
                prerequisite_changes[setting] = req_value

    for setting, req_value in SGX_REGISTRATION_SETTINGS.items():
        curr_value = current_settings.get(setting)
        if curr_value is None or str(curr_value).strip().lower() != str(req_value).strip().lower():
            registration_changes[setting] = req_value

    if sgx_factory_reset:
        curr_factory = current_settings.get("SgxFactoryReset", "Off")
        if curr_factory != "On":
            registration_changes["SgxFactoryReset"] = "On"

    has_prereq = len(prerequisite_changes) > 0
    has_tdx = len(tdx_dependent_changes) > 0 or len(tdx_attrs_missing) > 0
    has_reg = len(registration_changes) > 0

    if not has_prereq and not has_tdx and not has_reg:
        print("All required BIOS settings are already correct. No changes needed.")
        sys.exit(0)

    if has_prereq and has_tdx:
        print("Step 2: Two-phase remediation required.")
        payload_attributes = {**prerequisite_changes, **registration_changes}
        print(f"  Phase 1 (this run): {list(payload_attributes.keys())}")
        if tdx_dependent_changes:
            print(f"  Phase 2 (after reboot): {list(tdx_dependent_changes.keys())}")
        if tdx_attrs_missing:
            print(f"  Phase 2 — not yet visible (after reboot): {tdx_attrs_missing}")
        needs_rerun = True
    elif has_prereq or has_reg:
        payload_attributes = {**prerequisite_changes, **registration_changes}
        print(f"Step 2: Applying changes: {list(payload_attributes.keys())}")
        needs_rerun = False
    else:
        payload_attributes = tdx_dependent_changes
        print(f"Step 2: Applying TDX-dependent changes: {list(payload_attributes.keys())}")
        needs_rerun = False

    if dry_run:
        print("\n[DRY RUN] Would apply the following BIOS attribute changes:")
        for k, v in payload_attributes.items():
            curr = current_settings.get(k)
            print(f"  {k}: '{curr}' -> '{v}'")
        print("\n[DRY RUN] No changes applied. Remove --dry-run to apply.")
        sys.exit(0)

    print(f"\nApplying {len(payload_attributes)} setting(s)...")
    payload = {"Attributes": payload_attributes}
    try:
        patch_resp = requests.patch(
            settings_url, auth=(username, password), json=payload, verify=False, timeout=30
        )
        if patch_resp.status_code not in [200, 202]:
            print(f"PATCH operation failed: {patch_resp.status_code} - {patch_resp.text}")
            sys.exit(1)
        print("Staging changes accepted successfully.")
    except Exception as e:
        print(f"PATCH invocation error: {e}")
        sys.exit(1)

    print("Step 3: Creating iDRAC config job and initiating reboot...")
    reboot_payload = {
        "RebootJobType": "GracefulRebootWithPowerCycle",
        "TargetSettingsURI": "/redfish/v1/Systems/System.Embedded.1/Bios/Settings",
    }

    try:
        job_resp = requests.post(
            jobs_url, auth=(username, password), json=reboot_payload, verify=False, timeout=30
        )
        if job_resp.status_code in [200, 201, 202]:
            print("Remediation job scheduled. The server will now reboot.")
        else:
            print(f"Reboot / Job creation failed: {job_resp.status_code} - {job_resp.text}")
            sys.exit(1)
    except Exception as e:
        print(f"Job creation connection error: {e}")
        sys.exit(1)

    print()
    if needs_rerun:
        print("=" * 70)
        print("ACTION REQUIRED: Run this script again after the server reboots.")
        print()
        print("Prerequisite settings are being applied now. Once the reboot")
        print("completes (~15-20 min for bare metal), re-run this script to")
        print("apply TDX-specific settings that require TME-MT to be active.")
        print()
        print("This second run will trigger one additional reboot.")
        print("=" * 70)
    else:
        if "EnableTdxSeamldr" in payload_attributes:
            print("NOTE: A full power cycle (not just warm reboot) may be required")
            print("for the SEAM loader to initialize properly.")
        if "SgxAutoRegistrationAgent" in payload_attributes:
            print("NOTE: After reboot, the BIOS MPA will attempt to register")
            print("this platform with Intel. Ensure outbound HTTPS access to")
            print("Intel Registration Service is available during boot.")
        if "SgxFactoryReset" in payload_attributes:
            print("NOTE: SGX Factory Reset will re-generate platform keys and")
            print("trigger Initial Platform Establishment on next boot.")
        print("No further runs needed after this reboot.")


if __name__ == "__main__":
    args = parse_args()
    apply_tdx_settings(
        args.host, args.username, args.password,
        sgx_factory_reset=args.sgx_factory_reset,
        dry_run=args.dry_run,
    )

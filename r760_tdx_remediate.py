#!/usr/bin/env python3
import argparse
import os
import requests
import urllib3
import sys

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

REQUIRED_SETTINGS = {
    "NodeInterleaving": "Disabled",
    "X2ApicMode": "Enabled",
    "CpuPhysicalAddressLimit": "Disabled",
    "MemoryEncryption": "MultipleKeys",
    "GlobalMemoryIntegrity": "Disabled",
    "IntelTdx": "Enabled",
    "TmeMtTdxKeySplit": 1,
    "TdxSeamLoader": "Enabled",
    "IntelSgx": "Enabled",
    "IntelTxt": "On",
}

PREREQUISITE_GROUP = {
    "NodeInterleaving", "X2ApicMode", "CpuPhysicalAddressLimit",
    "MemoryEncryption", "GlobalMemoryIntegrity", "IntelSgx", "IntelTxt",
}

TDX_DEPENDENT_GROUP = {"IntelTdx", "TmeMtTdxKeySplit", "TdxSeamLoader"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Remediate Dell PowerEdge R760 BIOS settings for Intel TDX"
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
    args = parser.parse_args()
    if not args.host:
        parser.error("--host is required (or set IDRAC_HOST)")
    if not args.password:
        parser.error("--password is required (or set IDRAC_PASSWORD)")
    return args


def apply_tdx_settings(host, username, password):
    base_url = f"https://{host}/redfish/v1/Systems/System.Embedded.1/Bios"
    settings_url = f"{base_url}/Settings"
    jobs_url = f"https://{host}/redfish/v1/Managers/iDRAC.Embedded.1/JobService/Actions/JobService.CreateJobAndReboot"

    print(f"Step 1: Connecting to R760 iDRAC at {host} to verify current config...")
    try:
        response = requests.get(base_url, auth=(username, password), verify=False, timeout=15)
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

    for setting, req_value in REQUIRED_SETTINGS.items():
        curr_value = current_settings.get(setting)

        if setting in TDX_DEPENDENT_GROUP:
            if curr_value is None:
                tdx_attrs_missing.append(setting)
            elif str(curr_value).strip().lower() != str(req_value).strip().lower():
                tdx_dependent_changes[setting] = req_value
        else:
            if str(curr_value).strip().lower() != str(req_value).strip().lower():
                prerequisite_changes[setting] = req_value

    has_prereq = len(prerequisite_changes) > 0
    has_tdx = len(tdx_dependent_changes) > 0 or len(tdx_attrs_missing) > 0

    if not has_prereq and not has_tdx:
        print("All required BIOS settings are already correct. No changes needed.")
        sys.exit(0)

    if has_prereq and has_tdx:
        print(f"Step 2: Two-phase remediation required.")
        print(f"  Phase 1 (this run): {list(prerequisite_changes.keys())}")
        if tdx_dependent_changes:
            print(f"  Phase 2 (after reboot): {list(tdx_dependent_changes.keys())}")
        if tdx_attrs_missing:
            print(f"  Phase 2 — not yet visible (after reboot): {tdx_attrs_missing}")
        print()
        print("Applying prerequisite settings only (phase 1)...")
        payload_attributes = prerequisite_changes
        needs_rerun = True
    elif has_prereq:
        print(f"Step 2: Applying prerequisite changes: {list(prerequisite_changes.keys())}")
        payload_attributes = prerequisite_changes
        needs_rerun = False
    else:
        print(f"Step 2: Applying TDX-dependent changes: {list(tdx_dependent_changes.keys())}")
        payload_attributes = tdx_dependent_changes
        needs_rerun = False

    payload = {"Attributes": payload_attributes}
    try:
        patch_resp = requests.patch(settings_url, auth=(username, password), json=payload, verify=False, timeout=15)
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
        job_resp = requests.post(jobs_url, auth=(username, password), json=reboot_payload, verify=False, timeout=15)
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
        print("Prerequisite settings (TME-MT, SGX, etc.) are being applied now.")
        print("Once the reboot completes, re-run this script to apply the")
        print("TDX-specific settings (IntelTdx, TdxSeamLoader, TmeMtTdxKeySplit)")
        print("which require TME-MT to be active before they can be configured.")
        print()
        print("This second run will trigger one additional reboot.")
        print("=" * 70)
    else:
        if not has_prereq and "TdxSeamLoader" in tdx_dependent_changes:
            print("NOTE: A full power cycle (not just warm reboot) may be required")
            print("for the SEAM loader to initialize properly.")
        print("No further runs needed after this reboot.")


if __name__ == "__main__":
    args = parse_args()
    apply_tdx_settings(args.host, args.username, args.password)

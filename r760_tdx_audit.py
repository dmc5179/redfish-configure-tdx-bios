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
        description="Audit Dell PowerEdge R760 BIOS settings for Intel TDX readiness"
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


def audit_bios_settings(host, username, password):
    base_url = f"https://{host}/redfish/v1/Systems/System.Embedded.1/Bios"

    print(f"Connecting to R760 iDRAC at {host} for TDX configuration audit...")
    try:
        response = requests.get(base_url, auth=(username, password), verify=False, timeout=15)
        if response.status_code != 200:
            print(f"Error: Unable to fetch BIOS configurations. Status Code: {response.status_code}")
            sys.exit(1)
        current_settings = response.json().get("Attributes", {})
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    mismatches = 0
    prerequisite_changes = []
    tdx_dependent_changes = []
    tdx_attrs_missing = []

    print("\n=== INTEL TDX BIOS SETTINGS AUDIT REPORT (R760) ===\n")
    print("--- Prerequisite Settings ---")

    for setting in sorted(PREREQUISITE_GROUP):
        req_value = REQUIRED_SETTINGS[setting]
        curr_value = current_settings.get(setting)
        if str(curr_value).strip().lower() == str(req_value).strip().lower():
            print(f"  [PASS] {setting}: '{curr_value}'")
        else:
            print(f"  [FAIL] {setting}: '{curr_value}' -> needs '{req_value}'")
            prerequisite_changes.append(setting)
            mismatches += 1

    print("\n--- TDX-Dependent Settings ---")

    for setting in sorted(TDX_DEPENDENT_GROUP):
        req_value = REQUIRED_SETTINGS[setting]
        curr_value = current_settings.get(setting)
        if curr_value is None:
            print(f"  [MISS] {setting}: attribute not present in BIOS (requires TME-MT active)")
            tdx_attrs_missing.append(setting)
            mismatches += 1
        elif str(curr_value).strip().lower() == str(req_value).strip().lower():
            print(f"  [PASS] {setting}: '{curr_value}'")
        else:
            print(f"  [FAIL] {setting}: '{curr_value}' -> needs '{req_value}'")
            tdx_dependent_changes.append(setting)
            mismatches += 1

    print("\n=== REBOOT ANALYSIS ===")

    has_prereq_changes = len(prerequisite_changes) > 0
    has_tdx_changes = len(tdx_dependent_changes) > 0 or len(tdx_attrs_missing) > 0

    if not has_prereq_changes and not has_tdx_changes:
        reboots = 0
        print("No changes required. No reboot needed.")
    elif has_prereq_changes and has_tdx_changes:
        reboots = 2
        print(f"Prerequisite changes (reboot 1): {', '.join(prerequisite_changes)}")
        if tdx_dependent_changes:
            print(f"TDX-dependent changes (reboot 2): {', '.join(tdx_dependent_changes)}")
        if tdx_attrs_missing:
            print(f"TDX attributes not yet visible (reboot 2): {', '.join(tdx_attrs_missing)}")
            print("  These attributes will appear after MemoryEncryption=MultipleKeys")
            print("  is applied and the server completes a reboot.")
        print(f"\nESTIMATED REBOOTS REQUIRED: {reboots}")
    else:
        reboots = 1
        if has_prereq_changes:
            print(f"Prerequisite changes (reboot 1): {', '.join(prerequisite_changes)}")
        if tdx_dependent_changes:
            print(f"TDX-dependent changes (reboot 1): {', '.join(tdx_dependent_changes)}")
        if tdx_attrs_missing:
            print(f"TDX attributes not yet visible: {', '.join(tdx_attrs_missing)}")
        print(f"\nESTIMATED REBOOTS REQUIRED: {reboots}")

    if has_tdx_changes and "TdxSeamLoader" in (tdx_dependent_changes + tdx_attrs_missing):
        print("\nNOTE: Enabling the SEAM loader may require a full power cycle")
        print("(not just a warm reboot) for proper initialization.")

    if reboots > 0:
        print("\nOS IMPACT: Each reboot causes downtime for the operating system")
        print("and all workloads on this host. Drain or cordon the node first.")

    print("\n=== AUDIT SUMMARY ===")
    if mismatches == 0:
        print("All required BIOS settings are correctly configured for Intel TDX.")
    else:
        print(f"{mismatches} setting(s) must be corrected before Intel TDX can be enabled.")

    return mismatches


if __name__ == "__main__":
    args = parse_args()
    sys.exit(0 if audit_bios_settings(args.host, args.username, args.password) == 0 else 1)

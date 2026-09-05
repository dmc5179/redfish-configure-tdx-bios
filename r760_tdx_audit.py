#!/usr/bin/env python3
import argparse
import json
import os
import requests
import urllib3
import sys

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Attribute names for Dell PowerEdge R760 — NOT YET VERIFIED against live hardware.
# These are the same names as the R660 (BIOS 2.7.x). If the R760 uses different
# attribute names, update this map after running the audit and checking for MISS results.
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
        description="Audit Dell PowerEdge R760 BIOS settings for Intel TDX and SGX attestation readiness"
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
        "--json", "-j",
        action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()
    if not args.host:
        parser.error("--host is required (or set IDRAC_HOST)")
    if not args.password:
        parser.error("--password is required (or set IDRAC_PASSWORD)")
    return args


def fetch_bios_attributes(host, username, password):
    base_url = f"https://{host}/redfish/v1/Systems/System.Embedded.1/Bios"
    response = requests.get(base_url, auth=(username, password), verify=False, timeout=30)
    if response.status_code != 200:
        print(f"Error: Unable to fetch BIOS configurations. Status Code: {response.status_code}")
        sys.exit(1)
    return response.json().get("Attributes", {})


def fetch_system_info(host, username, password):
    url = f"https://{host}/redfish/v1/Systems/System.Embedded.1"
    response = requests.get(url, auth=(username, password), verify=False, timeout=30)
    if response.status_code != 200:
        return {}
    data = response.json()
    return {
        "Model": data.get("Model", "Unknown"),
        "BiosVersion": data.get("BiosVersion", "Unknown"),
        "Processor": data.get("ProcessorSummary", {}).get("Model", "Unknown"),
    }


def check_settings(current, required_map, group_label):
    results = []
    for setting in sorted(required_map):
        req_value = required_map[setting]
        curr_value = current.get(setting)
        if curr_value is None:
            results.append((setting, None, req_value, "MISS"))
        elif str(curr_value).strip().lower() == str(req_value).strip().lower():
            results.append((setting, curr_value, req_value, "PASS"))
        else:
            results.append((setting, curr_value, req_value, "FAIL"))
    return results


def audit_bios_settings(host, username, password, json_output=False):
    try:
        sys_info = fetch_system_info(host, username, password)
        current_settings = fetch_bios_attributes(host, username, password)
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    prereq_settings = {k: v for k, v in REQUIRED_SETTINGS.items() if k in PREREQUISITE_GROUP}
    tdx_settings = {k: v for k, v in REQUIRED_SETTINGS.items() if k in TDX_DEPENDENT_GROUP}

    prereq_results = check_settings(current_settings, prereq_settings, "Prerequisite")
    tdx_results = check_settings(current_settings, tdx_settings, "TDX-Dependent")
    reg_results = check_settings(current_settings, SGX_REGISTRATION_SETTINGS, "SGX Registration")

    all_results = prereq_results + tdx_results + reg_results
    mismatches = sum(1 for _, _, _, s in all_results if s != "PASS")

    if json_output:
        output = {
            "host": host,
            "system": sys_info,
            "tdx_ready": all(s == "PASS" for _, _, _, s in prereq_results + tdx_results),
            "attestation_ready": all(s == "PASS" for _, _, _, s in reg_results),
            "settings": {},
        }
        for name, curr, req, status in all_results:
            output["settings"][name] = {
                "current": curr, "required": req, "status": status,
            }
        print(json.dumps(output, indent=2))
        return mismatches

    model = sys_info.get("Model", "R760")
    bios_ver = sys_info.get("BiosVersion", "?")
    proc = sys_info.get("Processor", "?")
    print(f"Target: {model} | BIOS {bios_ver} | {proc}")
    print(f"iDRAC:  {host}")

    print(f"\n{'=' * 60}")
    print(f" INTEL TDX BIOS AUDIT REPORT")
    print(f"{'=' * 60}")

    print("\n--- Prerequisite Settings ---")
    for name, curr, req, status in prereq_results:
        if status == "PASS":
            print(f"  [PASS] {name}: '{curr}'")
        elif status == "MISS":
            print(f"  [MISS] {name}: not present in BIOS")
        else:
            print(f"  [FAIL] {name}: '{curr}' -> needs '{req}'")

    print("\n--- TDX-Dependent Settings ---")
    for name, curr, req, status in tdx_results:
        if status == "PASS":
            print(f"  [PASS] {name}: '{curr}'")
        elif status == "MISS":
            print(f"  [MISS] {name}: not present (requires TME-MT active)")
        else:
            print(f"  [FAIL] {name}: '{curr}' -> needs '{req}'")

    print("\n--- SGX Attestation Registration ---")
    for name, curr, req, status in reg_results:
        if status == "PASS":
            print(f"  [PASS] {name}: '{curr}'")
        elif status == "MISS":
            print(f"  [MISS] {name}: not present in BIOS")
        else:
            print(f"  [FAIL] {name}: '{curr}' -> needs '{req}'")

    sgx_factory = current_settings.get("SgxFactoryReset", "Off")
    print(f"  [INFO] SgxFactoryReset: '{sgx_factory}'")

    prereq_failures = [n for n, _, _, s in prereq_results if s != "PASS"]
    tdx_failures = [n for n, _, _, s in tdx_results if s != "PASS"]
    reg_failures = [n for n, _, _, s in reg_results if s != "PASS"]

    print(f"\n{'=' * 60}")
    print(f" REBOOT ANALYSIS")
    print(f"{'=' * 60}")

    tdx_ok = not prereq_failures and not tdx_failures
    reg_ok = not reg_failures

    if tdx_ok and reg_ok:
        print("No changes required. No reboot needed.")
    else:
        reboots = 0
        if prereq_failures and tdx_failures:
            reboots = 2
            print(f"Prerequisite changes (reboot 1): {', '.join(prereq_failures)}")
            print(f"TDX-dependent changes (reboot 2): {', '.join(tdx_failures)}")
            if not reg_ok:
                print(f"SGX registration changes (combined with reboot 1): {', '.join(reg_failures)}")
        elif prereq_failures or tdx_failures:
            reboots = 1
            changes = prereq_failures + tdx_failures
            print(f"TDX changes (reboot 1): {', '.join(changes)}")
            if not reg_ok:
                print(f"SGX registration changes (same reboot): {', '.join(reg_failures)}")
        elif not reg_ok:
            reboots = 1
            print(f"SGX registration changes (reboot 1): {', '.join(reg_failures)}")

        print(f"\nESTIMATED REBOOTS REQUIRED: {reboots}")

        if "EnableTdxSeamldr" in tdx_failures:
            print("\nNOTE: Enabling the SEAM loader may require a full power cycle")
            print("(not just a warm reboot) for proper initialization.")

        if not reg_ok:
            print("\nNOTE: After enabling SgxAutoRegistrationAgent, the BIOS will")
            print("attempt to register the platform with Intel on next boot.")
            print("This requires outbound HTTPS to Intel Registration Service.")
            print("After successful registration, PCK certificates become available")
            print("and TDX remote attestation can complete end-to-end.")
            if sgx_factory == "Off":
                print("\nIf this platform has never been registered, you may also need")
                print("to set SgxFactoryReset=On (one-time) to trigger Initial")
                print("Platform Establishment. Use --sgx-factory-reset with the")
                print("remediate script.")

        print("\nOS IMPACT: Each reboot causes downtime for the operating system")
        print("and all workloads on this host. Drain or cordon the node first.")

    print(f"\n{'=' * 60}")
    print(f" SUMMARY")
    print(f"{'=' * 60}")
    if tdx_ok:
        print("TDX:          READY")
    else:
        count = len(prereq_failures) + len(tdx_failures)
        print(f"TDX:          {count} setting(s) need correction")
    if reg_ok:
        print("Attestation:  READY (SGX registration configured)")
    else:
        print(f"Attestation:  {len(reg_failures)} setting(s) need correction")

    return mismatches


if __name__ == "__main__":
    args = parse_args()
    sys.exit(0 if audit_bios_settings(args.host, args.username, args.password, args.json) == 0 else 1)

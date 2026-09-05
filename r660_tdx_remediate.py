#!/usr/bin/env python3
import argparse
import os
import requests
import urllib3
import sys

from idrac_common import get_idrac_info, create_bios_config_job_and_reboot

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


def _check_pending_settings(host, username, password):
    """Return the current pending BIOS settings (staged for next reboot)."""
    url = f"https://{host}/redfish/v1/Systems/System.Embedded.1/Bios/Settings"
    resp = requests.get(url, auth=(username, password), verify=False, timeout=30)
    if resp.status_code != 200:
        return {}
    return resp.json().get("Attributes", {})


def _clear_pending_settings(host, username, password, keep_keys=None):
    """Clear pending BIOS settings by resetting them to current values.

    If keep_keys is provided, only those pending attributes are preserved.
    """
    pending = _check_pending_settings(host, username, password)
    if not pending:
        return

    base_url = f"https://{host}/redfish/v1/Systems/System.Embedded.1/Bios"
    resp = requests.get(base_url, auth=(username, password), verify=False, timeout=30)
    if resp.status_code != 200:
        return
    current = resp.json().get("Attributes", {})

    reset_attrs = {}
    for k, v in pending.items():
        if keep_keys and k in keep_keys:
            continue
        curr_val = current.get(k)
        if curr_val is not None and str(curr_val) != str(v):
            reset_attrs[k] = curr_val

    if reset_attrs:
        settings_url = f"{base_url}/Settings"
        requests.patch(
            settings_url, auth=(username, password),
            json={"Attributes": reset_attrs}, verify=False, timeout=30,
        )


def apply_tdx_settings(host, username, password, sgx_factory_reset=False, dry_run=False):
    base_url = f"https://{host}/redfish/v1/Systems/System.Embedded.1/Bios"
    settings_url = f"{base_url}/Settings"

    print(f"Step 1: Connecting to R660 iDRAC at {host}...")
    try:
        idrac_info = get_idrac_info(host, username, password)
        print(f"  iDRAC firmware: {idrac_info['FirmwareVersion']}")

        response = requests.get(base_url, auth=(username, password), verify=False, timeout=30)
        if response.status_code != 200:
            print(f"Error fetching BIOS attributes: {response.status_code}")
            sys.exit(1)
        current_settings = response.json().get("Attributes", {})
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    pending = _check_pending_settings(host, username, password)
    if pending:
        print(f"  Found {len(pending)} pending (staged) setting(s)")

    # BIOS dependency: when SgxFactoryReset is On (current or pending),
    # SgxAutoRegistrationAgent and SgxPackageInfoInBandAccess become read-only.
    # Must apply factory reset first, reboot, then set registration attributes.
    factory_reset_active = (
        current_settings.get("SgxFactoryReset") == "On"
        or pending.get("SgxFactoryReset") == "On"
    )

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
        if curr_factory != "On" and "SgxFactoryReset" not in pending:
            registration_changes["SgxFactoryReset"] = "On"

    has_prereq = len(prerequisite_changes) > 0
    has_tdx = len(tdx_dependent_changes) > 0 or len(tdx_attrs_missing) > 0
    has_reg = len(registration_changes) > 0

    if not has_prereq and not has_tdx and not has_reg:
        if factory_reset_active:
            print()
            print("SGX Factory Reset is pending. Registration attributes are locked")
            print("until the factory reset completes (next reboot).")
            print()
            if not dry_run:
                print("Creating config job to apply pending factory reset...")
                if not create_bios_config_job_and_reboot(host, username, password):
                    sys.exit(1)
                print()
                print("=" * 70)
                print("ACTION REQUIRED: Run this script again after the server reboots")
                print("(~15-20 min). The factory reset will complete, unlocking the")
                print("registration attributes for the next run.")
                print("=" * 70)
            else:
                print("[DRY RUN] Would create config job and reboot to apply factory reset.")
            sys.exit(0)
        print("All required BIOS settings are already correct. No changes needed.")
        sys.exit(0)

    # When factory reset is active, registration attributes are read-only.
    # Split them out and defer to the next run.
    deferred_registration = {}
    if factory_reset_active and has_reg:
        for k in list(registration_changes.keys()):
            if k in SGX_REGISTRATION_SETTINGS:
                deferred_registration[k] = registration_changes.pop(k)
        has_reg = len(registration_changes) > 0
        if deferred_registration:
            print()
            print(f"  NOTE: {list(deferred_registration.keys())} deferred — read-only while")
            print("  SgxFactoryReset is active. Will be applied on next run after reboot.")

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
        needs_rerun = bool(deferred_registration)
    else:
        payload_attributes = tdx_dependent_changes
        print(f"Step 2: Applying TDX-dependent changes: {list(payload_attributes.keys())}")
        needs_rerun = bool(deferred_registration)

    if not payload_attributes and factory_reset_active:
        print()
        print("No immediately applicable changes. Applying pending factory reset...")
        if not dry_run:
            if not create_bios_config_job_and_reboot(host, username, password):
                sys.exit(1)
            print()
            print("=" * 70)
            print("ACTION REQUIRED: Run this script again after the server reboots")
            print("(~15-20 min) to apply deferred registration settings.")
            print("=" * 70)
        else:
            print("[DRY RUN] Would create config job and reboot.")
        sys.exit(0)

    if dry_run:
        print("\n[DRY RUN] Would apply the following BIOS attribute changes:")
        for k, v in payload_attributes.items():
            curr = current_settings.get(k)
            print(f"  {k}: '{curr}' -> '{v}'")
        if deferred_registration:
            print(f"\n[DRY RUN] Deferred to next run (after factory reset reboot):")
            for k, v in deferred_registration.items():
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
    if not create_bios_config_job_and_reboot(host, username, password):
        sys.exit(1)

    print("\nRemediation job scheduled. The server will now reboot.")
    print()
    if needs_rerun or deferred_registration:
        print("=" * 70)
        print("ACTION REQUIRED: Run this script again after the server reboots.")
        print()
        if has_prereq and has_tdx:
            print("Prerequisite settings are being applied now. Once the reboot")
            print("completes (~15-20 min for bare metal), re-run this script to")
            print("apply TDX-specific settings that require TME-MT to be active.")
        if deferred_registration:
            print("SGX registration settings will be applied on the next run")
            print("(they are read-only while SgxFactoryReset is active).")
        print()
        print("Additional reboot(s) will be needed.")
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

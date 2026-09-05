"""Shared iDRAC Redfish helpers for TDX BIOS configuration scripts.

Handles firmware version detection and uses the correct job creation
endpoints for each iDRAC generation.
"""

import requests
import sys


def get_idrac_info(host, username, password):
    url = f"https://{host}/redfish/v1/Managers/iDRAC.Embedded.1"
    resp = requests.get(url, auth=(username, password), verify=False, timeout=30)
    if resp.status_code != 200:
        return {"FirmwareVersion": "unknown"}
    data = resp.json()
    return {
        "FirmwareVersion": data.get("FirmwareVersion", "unknown"),
        "Model": data.get("Model", "unknown"),
    }


def get_system_info(host, username, password):
    url = f"https://{host}/redfish/v1/Systems/System.Embedded.1"
    resp = requests.get(url, auth=(username, password), verify=False, timeout=30)
    if resp.status_code != 200:
        return {}
    data = resp.json()
    return {
        "Model": data.get("Model", "Unknown"),
        "BiosVersion": data.get("BiosVersion", "Unknown"),
        "Processor": data.get("ProcessorSummary", {}).get("Model", "Unknown"),
    }


def _try_legacy_job_and_reboot(host, username, password):
    """Try the legacy single-call CreateJobAndReboot action."""
    url = f"https://{host}/redfish/v1/Managers/iDRAC.Embedded.1/JobService/Actions/JobService.CreateJobAndReboot"
    payload = {
        "RebootJobType": "GracefulRebootWithPowerCycle",
        "TargetSettingsURI": "/redfish/v1/Systems/System.Embedded.1/Bios/Settings",
    }
    resp = requests.post(url, auth=(username, password), json=payload, verify=False, timeout=30)
    return resp


def _try_oem_dell_job_flow(host, username, password):
    """Use the Dell OEM job flow: create config job, then power cycle."""
    jobs_url = f"https://{host}/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/Jobs"
    job_payload = {
        "TargetSettingsURI": "/redfish/v1/Systems/System.Embedded.1/Bios/Settings",
    }
    resp = requests.post(jobs_url, auth=(username, password), json=job_payload, verify=False, timeout=30)
    if resp.status_code not in [200, 201, 202]:
        return resp

    job_id = None
    location = resp.headers.get("Location", "")
    if location:
        job_id = location.rsplit("/", 1)[-1]

    reset_url = f"https://{host}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset"
    reset_resp = requests.post(
        reset_url, auth=(username, password),
        json={"ResetType": "PowerCycle"},
        verify=False, timeout=30,
    )
    if reset_resp.status_code not in [200, 202, 204]:
        print(f"  Config job {job_id} created, but power cycle failed: "
              f"{reset_resp.status_code} - {reset_resp.text}")
        print(f"  You may need to manually power cycle the server.")
        return reset_resp

    resp.job_id = job_id
    return resp


def create_bios_config_job_and_reboot(host, username, password):
    """Create a BIOS config job and reboot, auto-detecting the correct method.

    Tries the legacy single-call endpoint first. If it returns 404 (not
    available on newer firmware), falls back to the Dell OEM two-step flow.
    """
    print("  Detecting iDRAC job service method...")

    resp = _try_legacy_job_and_reboot(host, username, password)
    if resp.status_code in [200, 201, 202]:
        print("  Used legacy JobService.CreateJobAndReboot (success).")
        return True

    if resp.status_code == 404:
        print("  Legacy endpoint not available, using Dell OEM job flow...")
        resp = _try_oem_dell_job_flow(host, username, password)
        if resp.status_code in [200, 201, 202, 204]:
            job_id = getattr(resp, "job_id", None)
            if job_id:
                print(f"  Config job {job_id} created and power cycle initiated.")
            else:
                print("  Config job created and power cycle initiated.")
            return True

    print(f"  Job creation failed: {resp.status_code} - {resp.text}")
    return False

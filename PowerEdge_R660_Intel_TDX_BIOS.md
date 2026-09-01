# Dell PowerEdge R660 - Intel TDX BIOS Settings

This document outlines the required BIOS configuration to enable Intel Trusted Domain Extensions (TDX) on a Dell PowerEdge R660 server.

## 1. Memory Settings
* **Node Interleaving:** Disabled

## 2. Processor Settings
* **x2APIC Mode:** Enabled
* **CPU Physical Address Limit:** Disabled

## 3. System Security Settings
* **Memory Encryption:** Multiple Keys
* **Global Memory Integrity:** Disabled
* **Intel Trusted Domain Extension (TDX):** Enabled
* **TME-MT/TDX Key Split:** Set to a non-zero value (typically `1` or higher depending on guest requirements)
* **TDX Secure Arbitration Mode Loader (SEAM):** Enabled
* **Intel SGX:** Enabled
* **Intel TXT:** On (if available)

## 4. Initialization & Activation
1. Access the iDRAC Virtual Console.
2. Restart the server and press **F2** to enter System Setup.
3. Apply the changes above under **System BIOS**.
4. Save and reboot. A full power cycle may be required for the SEAM loader to initialize.

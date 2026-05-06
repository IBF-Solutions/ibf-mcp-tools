---
name: Pending FortiGate audit actions
description: Open follow-ups from the May 2026 FortiGate audit — actions the user kicked off and expects me to come back to.
type: project
originSessionId: c080cb2a-76d3-44cb-bbc0-08b309443fc2
---
## Open: Printer (policy 908) traffic analysis — `set logtraffic all` enabled (started ~2026-05-04)

**What:** User chose Option A from the printer-investigation suggestion: enable `set logtraffic all` on firewall policy 908 ("Block Printer", Canon `10.10.40.225` / MAC `74:bf:c0:66:a2:dc`) so that denied traffic shows up in Graylog with destination IPs/FQDNs.

**Why:** Policy 908 normally has `set logtraffic disable` to avoid Graylog spam. Without it we have zero visibility into what the noisy printer (~420 hits/day) is trying to reach. User wants to identify the destinations, then revert.

**How to apply:**
1. When user signals "let's analyze now" (or after ~1–2 hours of dwell time), query Graylog for `srcip:10.10.40.225` (or `srcmac:"74:bf:c0:66:a2:dc"`) using `tools/graylog_search.py`. Group by `dstip` / `hostname` / `dstport` to find recurring endpoints.
2. **Remind the user to revert** policy 908 back to `set logtraffic disable` once analysis is done — this is non-negotiable per their request ("Nicht vergessen: Log danach wieder entfernen!").
3. Document findings in `analysis/reports/` (new file or appended to SECURITY_AUDIT.md).

Mark this memory removed once both steps (analysis done + logging reverted + user confirmed) are complete.

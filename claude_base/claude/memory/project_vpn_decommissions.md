---
name: VPN tunnel decommissions
description: FortiGate VPN tunnels scheduled for removal — skip remediation work on these.
type: project
originSessionId: c080cb2a-76d3-44cb-bbc0-08b309443fc2
---
`IBF-Pflach-gw` IPSec tunnel on FortiGate-120G is currently **down** and scheduled for **deletion around 2026-06-01** (4 weeks from 2026-05-04).

**Why:** User confirmed during 2026-05-04 audit. The tunnel uses IKE aggressive mode and would otherwise be a remediation target — but no point fixing crypto on a tunnel about to be deleted.

**How to apply:** When auditing or recommending changes to VPN/IPsec config, skip `IBF-Pflach-gw` recommendations. Suggest cleaning up the config block (phase1-interface, phase2-interface, related routes/policies, peer cert/PSK) once the peer is confirmed removed. Other tunnel concerns (e.g., `MaintenanceI` aggressive mode + DPD, `web1-hz-gw*-gre` SHA1+DH5) still stand.

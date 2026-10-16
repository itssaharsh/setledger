# Tag normalization — before/after (synthetic demo tags)

Model: gemma3:1b via local ollama. 14/25 tags normalized; 7 rows degraded to passthrough by the vocabulary guard.

| file | raw | suggestion | conf | method |
|---|---|---|---|---|
| 1000_Midnight_Harbour.wav | kolkata transit — MIDNIGHT_HARBOUR_FINAL_v2 | Kolkata Transit — Midnight Harbour | 0.90 | gemma |
| 1001_Velvet_Signal.wav | unknown — Low Orbit Club - velvet signal | Low Orbit Club — Velvet Signal | 0.80 | gemma |
| 1002_Static_Lantern.wav | unknown — Analog Debt - static lantern | Analog Debt — Static Lantern | 0.75 | gemma |
| 1003_Neon_Garden.wav | Night feat. Shift Audio — Neon Garden   master | (unchanged) | 0.00 | passthrough |
| 1004_Hollow_Meridian.wav | Delta feat. Verma — Hollow Meridian   master | (unchanged) | 0.00 | passthrough |
| 1005_Amber_Fracture.wav | Kolkata Transit — Amber Fracture | (unchanged) | 0.90 | gemma |
| 1006_Paper_Motor.wav | low orbit club — PAPER_MOTOR_FINAL_v2 | Low Orbit Club — Paper Motor | 0.90 | gemma |
| 1007_Glass_Circuit.wav | unknown — Analog Debt - glass circuit | Analog Debt — Glass Circuit | 0.75 | gemma |
| 1008_Iron_Tide.wav | NIGHT SHIFT AUDIO — Iron Tide (extended MIX) [320] | Night Shift Audio — Iron Tide [extended MIX] | 0.95 | gemma |
| 1009_Wild_Echo.wav | delta verma — WILD_ECHO_FINAL_v2 | (unchanged) | 0.00 | passthrough |
| 1010_Midnight_Circuit.wav | Kolkata feat. Transit — Midnight Circuit   master | (unchanged) | 0.00 | passthrough |
| 1011_Velvet_Tide.wav | Low Orbit Club — Velvet Tide | (unchanged) | 0.90 | gemma |
| 1012_Static_Echo.wav | unknown — Analog Debt - static echo | Analog Debt — Static Echo | 0.75 | gemma |
| 1013_Neon_Harbour.wav | unknown — Night Shift Audio - neon harbour | Night Shift Audio — Neon Harbour | 0.70 | gemma |
| 1014_Hollow_Signal.wav | unknown — Delta Verma - hollow signal | Delta Verma — Hollow Signal | 0.75 | gemma |
| 1015_Amber_Lantern.wav | unknown — Kolkata Transit - amber lantern | Kolkata Transit — Amber Lantern | 0.70 | gemma |
| 1016_Paper_Garden.wav | Low feat. Orbit Club — Paper Garden   master | (unchanged) | 0.00 | passthrough |
| 1017_Glass_Meridian.wav | analog debt — GLASS_MERIDIAN_FINAL_v2 | Analog Debt — Glass Meridian | 0.95 | gemma |
| 1018_Iron_Fracture.wav | Night Shift Audio — Iron Fracture | (unchanged) | 0.90 | gemma |
| 1019_Wild_Motor.wav | Delta feat. Verma — Wild Motor   master | (unchanged) | 0.00 | passthrough |
| 1020_Midnight_Meridian.wav | Kolkata feat. Transit — Midnight Meridian   master | (unchanged) | 0.00 | passthrough |
| 1021_Velvet_Fracture.wav | LOW ORBIT CLUB — Velvet Fracture (extended MIX) [320] | Low Orbit Club — Velvet Fracture [extended MIX] | 0.90 | gemma |
| 1022_Static_Motor.wav | analog debt — STATIC_MOTOR_FINAL_v2 | Analog Debt — Static Motor | 0.95 | gemma |
| 1023_Neon_Circuit.wav | Night Shift Audio — Neon Circuit | (unchanged) | 0.90 | gemma |
| 1024_Hollow_Tide.wav | unknown — Delta Verma - hollow tide | Delta Verma — Hollow Tide | 0.75 | gemma |

_Guard (two-sided): every word in a suggestion must already appear in the raw tag, and every non-noise word in the raw tag must survive into the suggestion — the model may reformat, never invent and never lose._
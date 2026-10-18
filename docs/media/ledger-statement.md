# Quarterly royalty statements (synthetic gigs)

Backend: **Tiger Data**, TimescaleDB hypertable `plays` + continuous aggregate `plays_monthly`. Recall driver: clap, nominating through pgvector on the same database. 5 builder-owned synthetic gigs, scanned for real in 158s total.

## Q3 2026 — 3 gigs

| track | plays | minutes | weakest evidence | via ≈ | registered |
|---|---|---|---|---|---|
| 1024_Hollow_Tide.wav | 3 | 3.0 | 0.87 |  | no — register to claim |
| 1005_Amber_Fracture.wav | 3 | 3.0 | 0.79 | yes | no — register to claim |
| 1019_Wild_Motor.wav | 2 | 2.0 | 0.78 | yes | no — register to claim |
| 1015_Amber_Lantern.wav | 2 | 1.9 | 0.94 | yes | no — register to claim |
| 1000_Midnight_Harbour.wav | 1 | 1.0 | 0.93 |  | YES |
| 1008_Iron_Tide.wav | 1 | 1.0 | 0.95 |  | no — register to claim |
| 1013_Neon_Harbour.wav | 1 | 1.0 | 0.99 |  | no — register to claim |
| 1022_Static_Motor.wav | 1 | 1.0 | 0.80 | yes | no — register to claim |
| 1002_Static_Lantern.wav | 1 | 0.9 | 0.97 |  | no — register to claim |
| 1018_Iron_Fracture.wav | 1 | 0.9 | 1.00 |  | no — register to claim |
| 1023_Neon_Circuit.wav | 1 | 0.9 | 0.97 |  | no — register to claim |
| 1017_Glass_Meridian.wav | 1 | 0.9 | 1.00 |  | YES |
| 1010_Midnight_Circuit.wav | 1 | 0.9 | 0.87 |  | YES |
| 1016_Paper_Garden.wav | 1 | 0.9 | 0.79 | yes | no — register to claim |
| 1001_Velvet_Signal.wav | 1 | 0.7 | 0.79 | yes | no — register to claim |

## Q4 2026 — 2 gigs

| track | plays | minutes | weakest evidence | via ≈ | registered |
|---|---|---|---|---|---|
| 1005_Amber_Fracture.wav | 2 | 1.9 | 0.80 | yes | no — register to claim |
| 1015_Amber_Lantern.wav | 1 | 1.0 | 0.93 |  | no — register to claim |
| 1007_Glass_Circuit.wav | 1 | 1.0 | 0.99 |  | no — register to claim |
| 1024_Hollow_Tide.wav | 1 | 1.0 | 0.96 |  | no — register to claim |
| 1011_Velvet_Tide.wav | 1 | 1.0 | 0.94 |  | no — register to claim |
| 1022_Static_Motor.wav | 1 | 1.0 | 0.95 |  | no — register to claim |
| 1001_Velvet_Signal.wav | 1 | 0.9 | 0.95 |  | no — register to claim |
| 1021_Velvet_Fracture.wav | 1 | 0.9 | 1.00 |  | no — register to claim |
| 1017_Glass_Meridian.wav | 1 | 0.9 | 0.95 |  | YES |
| 1000_Midnight_Harbour.wav | 1 | 0.9 | 0.81 | yes | YES |
| 1006_Paper_Motor.wav | 1 | 0.9 | 0.79 | yes | no — register to claim |
| 1003_Neon_Garden.wav | 1 | 0.9 | 0.96 |  | YES |
| 1016_Paper_Garden.wav | 1 | 0.9 | 0.80 | yes | no — register to claim |

## Scanner benchmark across all gigs

| tier | plays | found (≥50% of the play) | partial |
|---|---|---|---|
| clean | 25 | 25 | 0 |
| tempo-shifted | 10 | 10 | 0 |
| foreign (not in crate) | 5 | claimed: 0 | |

False claims across every gig: **0**.

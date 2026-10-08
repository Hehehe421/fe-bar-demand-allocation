# 01 generate data

> Executed by `tools/dbx_notebook.py` on Databricks cluster `1008-205814-qhbfm7tc` (01_generate_data), profile `vm-summer-classic`.
> Started 2026-10-08 21:32:30 UTC, finished 2026-10-08 21:33:51 UTC. Outputs below are captured verbatim from the cluster.

# Stage A (part 1) — Generate the synthetic landing zone

Runs the committed, seeded generator `src/datagen/generate.py` at full scale and
lands raw files into the Unity Catalog volume that the Lakeflow pipeline reads from.

**All data produced here is synthetic.** No real, customer or scraped data is used
anywhere in this build. The generator is deterministic: the same seed reproduces
the landing zone byte for byte.

What the business is: a licensed toy and collectibles manufacturer — collectible
plush and licensed action figures — selling wholesale B2B into three mass/e-tail
accounts, shipping from four domestic distribution centres, manufacturing offshore
on 60–110 day inbound lead times.

```python
import json
import subprocess
import sys

WS = "/Workspace/Users/sixuan.he@databricks.com/fe-bar-demand-allocation"
if WS + "/src" not in sys.path:
    sys.path.insert(0, WS + "/src")

# reportlab is used to emit the supplier PDFs; it is not in the ML runtime
subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q", "reportlab"],
    check=True,
    capture_output=True,
)

import config  # noqa: E402
from datagen.generate import CFG, generate  # noqa: E402

print("catalog :", config.CATALOG)
print("landing :", config.LANDING)
print("volume  :", config.VOLUME)
print()
print("generator config:")
for k, v in sorted(CFG.__dict__.items() if CFG.__dict__ else []):
    print(f"  {k} = {v}")
```

_Output:_

```text
catalog : toy_demand_allocation
landing : /Volumes/toy_demand_allocation/bronze/landing
volume  : toy_demand_allocation.bronze.landing

generator config:
  air_freight_usd_per_unit = 1.95
  chargeback_rate_on_miss = 0.8
  dup_row_rate = 0.006
  eol_clearance_rate = 0.3
  eol_markdown_rate = 0.22
  expedite_coverage = 0.6
  history_weeks = 156
  late_week_rate = 0.02
  lead_time_days_max = 110
  lead_time_days_min = 60
  n_product_images = 20
  n_skus = 2400
  n_supplier_docs = 12
  negative_qty_rate = 0.002
  null_onhand_rate = 0.015
  ocean_freight_usd_per_unit = 0.28
  orphan_sku_rate = 0.0015
  otif_date_miss_rate = 0.075
  otif_short_ship_rate = 0.05
  reorder_every_weeks = 2
  safety_weeks = 3.0
  seed = 20260408
  snapshot_days = 180
  supplier_short_rate = 0.08
  supplier_slip_rate = 0.25
  week_end_anchor = 2026-09-26
```

## Generator parameters

Every rate below is a knob recorded into `_manifest.json` beside the data, so the
run is self-documenting. The messiness rates exist so the Stage A data-quality
expectations have real violations to catch, and the failure-mode rates give the
KPI baseline genuine headroom to improve.

```python
import dataclasses  # noqa: E402

for f in dataclasses.fields(CFG):
    print(f"{f.name:32} {getattr(CFG, f.name)}")
```

_Output:_

```text
seed                             20260408
n_skus                           2400
history_weeks                    156
week_end_anchor                  2026-09-26
snapshot_days                    180
lead_time_days_min               60
lead_time_days_max               110
safety_weeks                     3.0
reorder_every_weeks              2
dup_row_rate                     0.006
late_week_rate                   0.02
null_onhand_rate                 0.015
negative_qty_rate                0.002
orphan_sku_rate                  0.0015
otif_date_miss_rate              0.075
otif_short_ship_rate             0.05
chargeback_rate_on_miss          0.8
eol_markdown_rate                0.22
eol_clearance_rate               0.3
expedite_coverage                0.6
ocean_freight_usd_per_unit       0.28
air_freight_usd_per_unit         1.95
supplier_short_rate              0.08
supplier_slip_rate               0.25
n_supplier_docs                  12
n_product_images                 20
```

## Generate

This writes CSV in several parts per source, so Auto Loader has a realistic
multi-file stream to ingest rather than one monolithic file.

```python
import time  # noqa: E402

t0 = time.time()
manifest = generate(config.LANDING)
print(f"generated in {time.time() - t0:.1f}s\n")
print(json.dumps(manifest, indent=2, default=str))
```

_Output:_

```text
generated in 44.1s

{
  "generated_at_utc": "2026-10-08T21:32:55.736389+00:00",
  "generator": "src/datagen/generate.py",
  "config": {
    "seed": 20260408,
    "n_skus": 2400,
    "history_weeks": 156,
    "week_end_anchor": "2026-09-26",
    "snapshot_days": 180,
    "lead_time_days_min": 60,
    "lead_time_days_max": 110,
    "safety_weeks": 3.0,
    "reorder_every_weeks": 2,
    "dup_row_rate": 0.006,
    "late_week_rate": 0.02,
    "null_onhand_rate": 0.015,
    "negative_qty_rate": 0.002,
    "orphan_sku_rate": 0.0015,
    "otif_date_miss_rate": 0.075,
    "otif_short_ship_rate": 0.05,
    "chargeback_rate_on_miss": 0.8,
    "eol_markdown_rate": 0.22,
    "eol_clearance_rate": 0.3,
    "expedite_coverage": 0.6,
    "ocean_freight_usd_per_unit": 0.28,
    "air_freight_usd_per_unit": 1.95,
    "supplier_short_rate": 0.08,
    "supplier_slip_rate": 0.25,
    "n_supplier_docs": 12,
    "n_product_images": 20
  },
  "data_is_synthetic": true,
  "week_range": [
    "2023-10-07",
    "2026-09-26"
  ],
  "sources": {
    "product_master": {
      "rows": 2400
    },
    "pos_weekly": {
      "etail_case_rows": 151807,
      "null_onhand_rows": 7187,
      "negative_qty_rows": 988,
      "late_arriving_rows": 9601,
      "duplicate_rows": 2887,
      "orphan_sku_rows": 721,
      "total_rows_written": 484779
    },
    "orders": {
      "total_rows_written": 255647,
      "negative_qty_rows": 500,
      "lines_with_chargeback": 28275
    },
    "inventory_snapshots": {
      "total_rows_written": 1212480,
      "distinct_pairs": 6736
    },
    "inbound_shipments": {
      "total_rows_written": 53344,
      "ocean_rows": 49731,
      "air_expedite_rows": 3613,
      "air_expedite_units": 1085487,
      "air_freight_usd": 2116699.65,
      "slipped_eta_rows": 9422,
      "short_shipped_rows": 3960
    },
    "markdown_clearance": {
      "total_rows_written": 11322,
      "cleared_units": 2063198,
      "markdown_loss_usd": 7940809.19
    },
    "supplier_docs": {
      "files_written": 12,
      "dir": "/Volumes/toy_demand_allocation/bronze/landing/supplier_docs"
    },
    "product_images": {
      "files_written": 20,
      "dir": "/Volumes/toy_demand_allocation/bronze/landing/product_images"
    }
  },
  "triplets_sku_retailer_dc": 9398,
  "elapsed_seconds": 43.7
}
```

## Verify what actually landed in the volume

Row counts and file counts read back off the volume, not from the generator's own
return value — so this is independent confirmation that the bytes are there.

```python
import os  # noqa: E402

total_files = 0
total_bytes = 0
rows = []
for src in sorted(os.listdir(config.LANDING)):
    d = os.path.join(config.LANDING, src)
    if not os.path.isdir(d):
        continue
    files = sorted(os.listdir(d))
    n_bytes = sum(os.path.getsize(os.path.join(d, f)) for f in files)
    total_files += len(files)
    total_bytes += n_bytes
    rows.append((src, len(files), f"{n_bytes / 1e6:.1f} MB", files[0] if files else "-"))

print(f"{'source':24} {'files':>6} {'size':>10}  first file")
print("-" * 78)
for r in rows:
    print(f"{r[0]:24} {r[1]:>6} {r[2]:>10}  {r[3]}")
print("-" * 78)
print(f"{'TOTAL':24} {total_files:>6} {total_bytes / 1e6:>9.1f} MB")
```

_Output:_

```text
source                    files       size  first file
------------------------------------------------------------------------------
inbound_shipments             6     4.9 MB  inbound_shipments_part000.csv
inventory_snapshots          10    43.1 MB  inventory_snapshots_part000.csv
markdown_clearance            4     0.7 MB  markdown_clearance_part000.csv
orders                        8    22.8 MB  orders_part000.csv
pos_weekly                   12    35.5 MB  pos_weekly_part000.csv
product_images               21     0.0 MB  SKU-00045_violet.png
product_master                1     0.4 MB  product_master.csv
supplier_docs                13     0.0 MB  _doc_index.json
------------------------------------------------------------------------------
TOTAL                        75     107.5 MB
```

## Sample each tabular source

A few rows of every source, so the shape and the deliberate messiness are visible
as text in this notebook.

```python
import pandas as pd  # noqa: E402

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 40)

for src in ("product_master", "pos_weekly", "orders", "inventory_snapshots",
            "inbound_shipments", "markdown_clearance"):
    d = os.path.join(config.LANDING, src)
    f = sorted(x for x in os.listdir(d) if x.endswith(".csv"))[0]
    df = pd.read_csv(os.path.join(d, f), nrows=4)
    print(f"\n=== {src}  ({f}) ===")
    print(df.to_string(index=False))
```

_Output:_

```text
=== product_master  (product_master.csv) ===
   sku_id                  product_name       brand      category license_family lifecycle_stage  case_pack  unit_cost_usd  msrp_usd  wholesale_price_usd  launch_week_index  planned_lifespan_weeks  figure_type                                  material_composition country_of_origin  gross_margin_pct
SKU-00001       Titan Squad Plushie 612 Titan Squad ACTION_FIGURE      EVERGREEN          MATURE          6           9.20     33.84                15.49                 -3                     206     HUMANOID ABS/polycarbonate blend, zinc-alloy articulation pins                VN            0.4061
SKU-00002          Titan Squad Mini 505 Titan Squad ACTION_FIGURE   SOCIAL_TREND          LAUNCH          6           5.82     15.22                 7.90                153                      27     CREATURE        100% polyester plush pile, PP pellet weighting                VN            0.2633
SKU-00003          Snugglewick Mini 300 Snugglewick         PLUSH      EVERGREEN          MATURE          8           7.23     22.01                11.02                  4                     256 NON_HUMANOID ABS/polycarbonate blend, zinc-alloy articulation pins                VN            0.3439
SKU-00004 Snugglewick Collector Set 396 Snugglewick         PLUSH      EVERGREEN          MATURE          6           7.28     23.14                11.60                 19                     245     CREATURE ABS/polycarbonate blend, zinc-alloy articulation pins                CN            0.3724

=== pos_weekly  (pos_weekly_part000.csv) ===
   sku_id retailer_code dc_code week_end_date  pos_units  true_demand_units  promo_flag  on_hand_units reported_uom         reported_at
SKU-00941       ETAIL_C DC_EAST    2024-10-26          2                 13           0            4.0         CASE 2024-10-27 00:00:00
SKU-01720        MASS_A DC_WEST    2026-01-10          1                  1           0            3.0         EACH 2026-01-12 00:00:00
SKU-02338        MASS_B DC_WEST    2026-02-14         16                 16           0           38.0         EACH 2026-02-17 00:00:00
SKU-01929        MASS_B DC_EAST    2026-02-07          9                  9           0           24.0         EACH 2026-02-10 00:00:00

=== orders  (orders_part000.csv) ===
 po_line_id    sku_id retailer_code  dc_code requested_week_end_date  requested_units  confirmed_units  shipped_units requested_ship_date actual_ship_date  wholesale_price_usd  chargeback_usd
PO-00000001 SKU-00001        MASS_B  DC_EAST              2023-10-21               12               12             12          2023-10-21       2023-10-21                15.49             0.0
PO-00000002 SKU-00001        MASS_B DC_SOUTH              2023-10-21               18               18             18          2023-10-21       2023-10-23                15.49             0.0
PO-00000003 SKU-00001        MASS_B  DC_WEST              2023-10-21                6                6              6          2023-10-21       2023-10-23                15.49             0.0
PO-00000004 SKU-00001       ETAIL_C  DC_WEST              2023-10-21               18               18             18          2023-10-21       2023-10-22                15.49             0.0

=== inventory_snapshots  (inventory_snapshots_part000.csv) ===
snapshot_date    sku_id    dc_code  on_hand_units  in_transit_units
   2026-03-31 SKU-00001    DC_EAST            678               288
   2026-03-31 SKU-00001   DC_SOUTH            121                 0
   2026-03-31 SKU-00001    DC_WEST            190                 0
   2026-03-31 SKU-00002 DC_CENTRAL              8                 0

=== inbound_shipments  (inbound_shipments_part000.csv) ===
     asn_id    sku_id    dc_code order_placed_date  ordered_units  shipped_units original_eta_date revised_eta_date  lead_time_days_planned  lead_time_days_actual origin_country transport_mode  freight_cost_usd
ASN-0000001 SKU-00001   DC_SOUTH        2023-10-07             66             66        2023-12-23       2023-12-23                      77                     77             VN          OCEAN             18.48
ASN-0000002 SKU-00009 DC_CENTRAL        2023-10-07             12             12        2023-12-16       2023-12-16                      70                     70             VN          OCEAN              3.36
ASN-0000003 SKU-00030 DC_CENTRAL        2023-10-07             12             12        2023-12-30       2024-01-20                      84                    105             VN          OCEAN              3.36
ASN-0000004 SKU-00030    DC_EAST        2023-10-07             36             36        2024-01-27       2024-01-27                     112                    112             VN          OCEAN             10.08

=== markdown_clearance  (markdown_clearance_part000.csv) ===
clearance_id    sku_id dc_code week_end_date  cleared_units  wholesale_price_usd  clearance_price_usd  unit_cost_usd  markdown_loss_usd
  MD-0004056 SKU-00852 DC_EAST    2025-10-04              8                 3.24                 1.59           2.27              13.20
  MD-0004057 SKU-00852 DC_EAST    2025-10-11              7                 3.24                 2.25           2.27               6.93
  MD-0003450 SKU-00736 DC_EAST    2025-12-20             13                19.23                 7.73          11.00             149.50
  MD-0003451 SKU-00736 DC_EAST    2025-12-27              9                19.23                11.31          11.00              71.28
```

## The deliberate messiness, shown explicitly

These are the violations the Silver layer's expectations must catch. Counting them
here means the Stage A event-log numbers can be checked against a known truth
rather than taken on faith.

```python
import glob  # noqa: E402

pos = pd.concat(
    [pd.read_csv(f) for f in sorted(glob.glob(f"{config.LANDING}/pos_weekly/*.csv"))],
    ignore_index=True,
)
pm = pd.read_csv(f"{config.LANDING}/product_master/product_master.csv")

key = ["sku_id", "retailer_code", "dc_code", "week_end_date"]
checks = {
    "rows_total": len(pos),
    "negative_pos_units": int((pos.pos_units < 0).sum()),
    "null_on_hand": int(pos.on_hand_units.isna().sum()),
    "duplicate_grain_keys": int(len(pos) - len(pos.drop_duplicates(subset=key))),
    "orphan_sku_refs": int((~pos.sku_id.isin(set(pm.sku_id))).sum()),
    "etail_c_rows_in_cases": int((pos.reported_uom == "CASE").sum()),
    "late_arriving_gt_8d": int(
        (
            (pd.to_datetime(pos.reported_at) - pd.to_datetime(pos.week_end_date)).dt.days > 8
        ).sum()
    ),
}
for k, v in checks.items():
    print(f"{k:26} {v:>10,}")

print("\nreported_uom distribution by retailer:")
print(pos.groupby(["retailer_code", "reported_uom"]).size().to_string())
```

_Output:_

```text
rows_total                    484,779
negative_pos_units                997
null_on_hand                    7,228
duplicate_grain_keys            2,887
orphan_sku_refs                   721
etail_c_rows_in_cases         152,936
late_arriving_gt_8d             9,678

reported_uom distribution by retailer:
retailer_code  reported_uom
ETAIL_C        CASE            152936
MASS_A         EACH            167148
MASS_B         EACH            164695
```

## Unstructured sources

The supplier PDFs and product images are not decoration: the PDFs feed the agent's
document-search tool in Stage C2, and the images carry a known figure-type and
dominant-colour ground truth.

```python
docs = json.load(open(f"{config.LANDING}/supplier_docs/_doc_index.json"))
imgs = json.load(open(f"{config.LANDING}/product_images/_image_index.json"))

print(f"supplier_docs: {len(docs)} PDFs")
for d in docs[:4]:
    print(f"  {d['file']:52} {d['kind']:10} {d['supplier']:32} terms={d['terms']}")
print(f"\nproduct_images: {len(imgs)} PNGs")
for i in imgs[:5]:
    print(f"  {i['file']:34} colour={i['dominant_color_label']:9} "
          f"silhouette={i['silhouette']:14} truth={i['figure_type_truth']}")

sil = {}
for i in imgs:
    sil[i["silhouette"]] = sil.get(i["silhouette"], 0) + 1
print("\nsilhouette ground truth:", sil)
```

_Output:_

```text
supplier_docs: 12 PDFs
  invoice_pearl_2026_01.pdf                            invoice    Pearl River Plush Works          terms=NET 60
  spec_sheet_summit_2026_02.pdf                        spec_sheet Summit Mould & Figure Co.        terms=NET 60
  invoice_lotus_2026_03.pdf                            invoice    Lotus Craft Toy Manufacturing    terms=NET 45
  spec_sheet_highland_2026_04.pdf                      spec_sheet Highland Softgoods Ltd.          terms=NET 60

product_images: 20 PNGs
  SKU-00430_coral.png                colour=coral     silhouette=NON_HUMANOID   truth=CREATURE
  SKU-00068_teal.png                 colour=teal      silhouette=NON_HUMANOID   truth=NON_HUMANOID
  SKU-00216_mustard.png              colour=mustard   silhouette=NON_HUMANOID   truth=NON_HUMANOID
  SKU-01818_indigo.png               colour=indigo    silhouette=NON_HUMANOID   truth=VEHICLE
  SKU-02277_rose.png                 colour=rose      silhouette=NON_HUMANOID   truth=CREATURE

silhouette ground truth: {'NON_HUMANOID': 19, 'HUMANOID': 1}
```

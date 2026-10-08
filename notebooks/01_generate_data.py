# %% [markdown]
# # Stage A (part 1) — Generate the synthetic landing zone
#
# Runs the committed, seeded generator `src/datagen/generate.py` at full scale and
# lands raw files into the Unity Catalog volume that the Lakeflow pipeline reads from.
#
# **All data produced here is synthetic.** No real, customer or scraped data is used
# anywhere in this build. The generator is deterministic: the same seed reproduces
# the landing zone byte for byte.
#
# What the business is: a licensed toy and collectibles manufacturer — collectible
# plush and licensed action figures — selling wholesale B2B into three mass/e-tail
# accounts, shipping from four domestic distribution centres, manufacturing offshore
# on 60–110 day inbound lead times.

# %%
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

# %% [markdown]
# ## Generator parameters
#
# Every rate below is a knob recorded into `_manifest.json` beside the data, so the
# run is self-documenting. The messiness rates exist so the Stage A data-quality
# expectations have real violations to catch, and the failure-mode rates give the
# KPI baseline genuine headroom to improve.

# %%
import dataclasses  # noqa: E402

for f in dataclasses.fields(CFG):
    print(f"{f.name:32} {getattr(CFG, f.name)}")

# %% [markdown]
# ## Generate
#
# This writes CSV in several parts per source, so Auto Loader has a realistic
# multi-file stream to ingest rather than one monolithic file.

# %%
import time  # noqa: E402

t0 = time.time()
manifest = generate(config.LANDING)
print(f"generated in {time.time() - t0:.1f}s\n")
print(json.dumps(manifest, indent=2, default=str))

# %% [markdown]
# ## Verify what actually landed in the volume
#
# Row counts and file counts read back off the volume, not from the generator's own
# return value — so this is independent confirmation that the bytes are there.

# %%
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

# %% [markdown]
# ## Sample each tabular source
#
# A few rows of every source, so the shape and the deliberate messiness are visible
# as text in this notebook.

# %%
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

# %% [markdown]
# ## The deliberate messiness, shown explicitly
#
# These are the violations the Silver layer's expectations must catch. Counting them
# here means the Stage A event-log numbers can be checked against a known truth
# rather than taken on faith.

# %%
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

# %% [markdown]
# ## Unstructured sources
#
# The supplier PDFs and product images are not decoration: the PDFs feed the agent's
# document-search tool in Stage C2, and the images carry a known figure-type and
# dominant-colour ground truth.

# %%
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

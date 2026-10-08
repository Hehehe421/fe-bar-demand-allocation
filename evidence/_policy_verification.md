### My own identity (catalog owner, workspace admin)

```text
current_user              in_admins
------------------------  ---------
sixuan.he@databricks.com  false    
(1 row)
```

#### Column mask as the catalog owner

```text
current_user              sku_id     brand        unit_cost_usd  gross_margin_pct  wholesale_price_usd
------------------------  ---------  -----------  -------------  ----------------  -------------------
sixuan.he@databricks.com  SKU-00001  Titan Squad  NULL           NULL              15.49              
sixuan.he@databricks.com  SKU-00002  Titan Squad  NULL           NULL              7.9                
sixuan.he@databricks.com  SKU-00003  Snugglewick  NULL           NULL              11.02              
(3 rows)
```

#### Row filter as the catalog owner

```text
current_user              retailer_code  rows_visible  pos_units
------------------------  -------------  ------------  ---------
sixuan.he@databricks.com  ETAIL_C        151474        3439980  
sixuan.he@databricks.com  MASS_A         165573        3031945  
sixuan.he@databricks.com  MASS_B         163136        3366123  
(3 rows)
```

### Persona `toy-planner-mass-a` (service principal `5d9eb1c3-888e-4acc-8625-4ecedea0fea5`)

#### Column mask on `unit_cost_usd` / `gross_margin_pct`

```text
  current_user                          sku_id     brand        unit_cost_usd  gross_margin_pct  wholesale_price_usd
  ------------------------------------  ---------  -----------  -------------  ----------------  -------------------
  5d9eb1c3-888e-4acc-8625-4ecedea0fea5  SKU-00001  Titan Squad  NULL           NULL              15.49              
  5d9eb1c3-888e-4acc-8625-4ecedea0fea5  SKU-00002  Titan Squad  NULL           NULL              7.9                
  5d9eb1c3-888e-4acc-8625-4ecedea0fea5  SKU-00003  Snugglewick  NULL           NULL              11.02              
```

#### Row filter on `retailer_code`

```text
  current_user                          retailer_code  rows_visible  pos_units
  ------------------------------------  -------------  ------------  ---------
  5d9eb1c3-888e-4acc-8625-4ecedea0fea5  MASS_A         165573        3031945  
```

### Persona `toy-data-scientist` (service principal `7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8`)

#### Column mask on `unit_cost_usd` / `gross_margin_pct`

```text
  current_user                          sku_id     brand        unit_cost_usd  gross_margin_pct  wholesale_price_usd
  ------------------------------------  ---------  -----------  -------------  ----------------  -------------------
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  SKU-00001  Titan Squad  NULL           NULL              15.49              
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  SKU-00002  Titan Squad  NULL           NULL              7.9                
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  SKU-00003  Snugglewick  NULL           NULL              11.02              
```

#### Row filter on `retailer_code`

```text
  current_user                          retailer_code  rows_visible  pos_units
  ------------------------------------  -------------  ------------  ---------
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  ETAIL_C        151474        3439980  
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  MASS_A         165573        3031945  
  7057da6f-60c0-4d8c-936c-c3ea1cdf9fc8  MASS_B         163136        3366123  
```

### Persona `toy-finance` (service principal `dd70e525-c0aa-4ca5-ade2-29671715871a`)

#### Column mask on `unit_cost_usd` / `gross_margin_pct`

```text
  current_user                          sku_id     brand        unit_cost_usd  gross_margin_pct  wholesale_price_usd
  ------------------------------------  ---------  -----------  -------------  ----------------  -------------------
  dd70e525-c0aa-4ca5-ade2-29671715871a  SKU-00001  Titan Squad  9.2            0.4061            15.49              
  dd70e525-c0aa-4ca5-ade2-29671715871a  SKU-00002  Titan Squad  5.82           0.2633            7.9                
  dd70e525-c0aa-4ca5-ade2-29671715871a  SKU-00003  Snugglewick  7.23           0.3439            11.02              
```

#### Row filter on `retailer_code`

```text
  current_user                          retailer_code  rows_visible  pos_units
  ------------------------------------  -------------  ------------  ---------
  dd70e525-c0aa-4ca5-ade2-29671715871a  ETAIL_C        151474        3439980  
  dd70e525-c0aa-4ca5-ade2-29671715871a  MASS_A         165573        3031945  
  dd70e525-c0aa-4ca5-ade2-29671715871a  MASS_B         163136        3366123  
```

### Persona `toy-app-service` (service principal `1c73204c-6b49-4db0-a860-300ba392393e`)

#### Column mask on `unit_cost_usd` / `gross_margin_pct`

```text
  current_user                          sku_id     brand        unit_cost_usd  gross_margin_pct  wholesale_price_usd
  ------------------------------------  ---------  -----------  -------------  ----------------  -------------------
  1c73204c-6b49-4db0-a860-300ba392393e  SKU-00001  Titan Squad  NULL           NULL              15.49              
  1c73204c-6b49-4db0-a860-300ba392393e  SKU-00002  Titan Squad  NULL           NULL              7.9                
  1c73204c-6b49-4db0-a860-300ba392393e  SKU-00003  Snugglewick  NULL           NULL              11.02              
```

#### Row filter on `retailer_code`

```text
  current_user                          retailer_code  rows_visible  pos_units
  ------------------------------------  -------------  ------------  ---------
  1c73204c-6b49-4db0-a860-300ba392393e  ETAIL_C        151474        3439980  
  1c73204c-6b49-4db0-a860-300ba392393e  MASS_A         165573        3031945  
  1c73204c-6b49-4db0-a860-300ba392393e  MASS_B         163136        3366123  
```

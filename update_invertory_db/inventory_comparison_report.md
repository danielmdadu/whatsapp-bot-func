# Reporte de Comparación de Inventarios

**Generado:** 2026-02-27  
**Inventario técnico:** `inventory_data.py` (62 máquinas)  
**Inventario de precios:** `prices_inventory.txt` (50 productos)  
**Mapeo de códigos:** `model_code_mapping.py` (33 relaciones definidas)

---

## Resumen

| Status | Cantidad |
|--------|----------|
| ✅ **En ambos inventarios** | 37 máquinas |
| ⚠️ **Solo en inventario técnico** | 25 máquinas |
| 🆕 **Solo en inventario de precios** | 13 productos |

---

## ✅ Máquinas en AMBOS Inventarios (37)

Relacionadas mediante `model_code_mapping.py` o coincidencia directa de nombre/código.

### Apisonador (1)
| Modelo (inventory_data.py) | Código SQL | Precio USD | Stock |
|----------------------------|-----------|------------|-------|
| Sakai RS75 | RS75 | $2,986 | 15 |

### Compresor Eléctrico (6)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| AIRMAN SAS22RD6E | SAS22RD6E | $18,571 | 1 |
| AIRMAN SAS37RD6E | SAS37RD6E | $25,226 | 2 |
| AIRMAN SAS4SD6C | SAS4SD6C | $7,498 | 2 |
| AIRMAN SAS55RD6E | SAS55RD6E | $36,290 | 2 |
| AIRMAN SAS75RD6E | SAS75RD6E | $40,555 | 2 |
| AIRMAN SAS8SD6C | SAS8SD6C | $11,009 | 1 |

### Compresor Portátil (1)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| AIRMAN PDS750S-4B1 | PDS750S4B1 | $87,428 | 3 |

### Rompedor / Martillo Neumático (3)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| Toku TCB-300 | TCB300 | $1,299 | 41 |
| Toku TPB-60 | TPB60 | $1,503 | 72 |
| Toku TPB-90 | TPB90 | $1,582 | 39 |

### Motobomba (2)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| Koshin KTY-100D | KTY100D | $4,553 | 19 |
| Koshin KTH-100 X | KTH100XBAF | $2,344 | 10 |

### Generador (6)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| Shindaiwa DG100MI-400 | DG100MI400 | $34,973 | 1 |
| Shindaiwa DGM250MK-D | DGM250MKD | $18,844 | 5 |
| Shindaiwa DGM450MK-D | DGM450MKD | $24,145 | 25 |
| Shindaiwa DGM600MK-D | DGM600MKD | $29,660 | 7 |
| Koshin GV-5500s | GV5500S | $1,476 | 53 |
| Koshin GV-8000S | GV8000S | $1,862 | 9 |

> [!NOTE]
> `Shindaiwa DG100MI-400` ↔ `DG100MI400`: coincidencia directa, **no está en `model_code_mapping.py`** — considerar agregarlo.

### Soldadora (3)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| Shindaiwa DGW400DMK | DGW400DMKD | $20,469 | 2 |
| Shindaiwa DGW500DM | DGW500DM200 | $24,882 | 9 |
| Shindaiwa EGW185MS | EGW185MS | $5,326 | 2 |

> [!NOTE]
> `Shindaiwa DGW500DM` ↔ `DGW500DM200` y `Shindaiwa EGW185MS` ↔ `EGW185MS`: coincidencias directas, **no están en `model_code_mapping.py`** — considerar agregarlos.

### Manipulador (1)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| LGMG H1840 | H1840 | $128,329 | 1 |

### Plataforma Articulada (4)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| LGMG A45JE-LI | A45JELI | $56,104 | 6 |
| LGMG AR52J | AR52J | $75,844 | 2 |
| LGMG AR60J-2 | AR60J-2 | $92,284 | 1 |
| LGMG AR60JE-2 | AR60JE-2 | $90,724 | 1 |

### Plataforma Tijera (4)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| LGMG SS1230E | SS1230E | $10,235 | 4 |
| LGMG S1932EII | S1932EII | $17,187 | 1 |
| LGMG S2632E II | S2632EII | $19,368 | 1 |
| LGMG S4046E II | S4046EII | $23,012 | 1 |

### Plataforma Unipersonal (2)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| LGMG MP0607SE | MP0607SE | $6,776 | 1 |
| LGMG MP1208SE | MP1208SE | $10,477 | 4 |

> [!NOTE]
> `LGMG MP0607SE` y `LGMG MP1208SE`: coincidencias directas, **no están en `model_code_mapping.py`** — considerar agregarlos.

### Torre de Iluminación (2)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| Shindaiwa SL433IDG-B/S1W | SL433IDGBS | $12,597 | 23 |
| Trime X-START | XSTART | $14,949 | 22 |

### Cortadora de Varillas (1)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| Simpedil C54 EVO | C54TTF05 | $10,912 | 19 |

### Dobladora de Varillas (1)
| Modelo | Código SQL | Precio USD | Stock |
|--------|-----------|------------|-------|
| Simpedil P54 EVO | P54TTF06 | $9,275 | 21 |

---

## ⚠️ Máquinas SOLO en Inventario Técnico (25)

Estas máquinas existen en `inventory_data.py` pero **NO** tienen equivalente en la base de precios SQL.

### Soldadora (1)
| Modelo | Categoría |
|--------|-----------|
| Shindaiwa DGW340DM | soldadora |

### Compresor Eléctrico (3)
| Modelo | Caudal máx. (CFM) |
|--------|--------------------|
| AIRMAN SAS75VD-E | 501.47 |
| AIRMAN SAS55VD-E | 367.27 |
| AIRMAN SAS37VD-E | 247.20 |

### Compresor Portátil (5)
| Modelo | Caudal máx. (CFM) |
|--------|--------------------|
| AIRMAN PDSF830S | 830 |
| AIRMAN PDSG750VRS-4C5 | 750–900 |
| AIRMAN PDS400S | 400 |
| AIRMAN PDSF375S-DP | 375 |
| AIRMAN PDS185S-6C2 | 185 |

### Compresor Eléctrico — con mapeo inválido (1)
| Modelo | Problema |
|--------|----------|
| AIRMAN SAS15RD6E | No tiene código SQL en inventario de precios |

### Generador (3)
| Modelo | Tipo | Potencia |
|--------|------|----------|
| Shindaiwa DGM150BMK | estacionario | 15 kVA |
| AIRMAN SDG100S | estacionario | 100 kVA |
| AIRMAN SDG150S | estacionario | 150 kVA |

> [!WARNING]
> `AIRMAN SDG150S` tiene el mapeo `SDG150S3A6` en `model_code_mapping.py`, pero ese código **no existe** en `prices_inventory.txt`. El mapeo es inválido.

### Montacargas (2)
| Modelo | Capacidad | Alimentación |
|--------|-----------|--------------|
| LGMG CPD30 | 3,000 kg | Eléctrica |
| LGMG CPD25 | 2,500 kg | Eléctrica |

> [!WARNING]
> `LGMG CPD30` tiene el mapeo `CPD30` en `model_code_mapping.py`, pero ese código **no existe** en `prices_inventory.txt`. El mapeo es inválido.

### Manipulador (2)
| Modelo | Altura máx. | Capacidad |
|--------|-------------|-----------|
| LGMG H625 | 5.94 m | 2,500 kg |
| LGMG H735 | 7.00 m | 3,500 kg |

### Plataforma Articulada (3)
| Modelo | Altura trabajo | Alimentación |
|--------|----------------|--------------|
| LGMG AR65J | 21.58 m | combustible |
| LGMG AR65JE-LI | 21.58 m | eléctrica |
| LGMG A30JE | 11.00 m | eléctrica |

### Plataforma Tijera (3)
| Modelo | Altura trabajo | Alimentación |
|--------|----------------|--------------|
| LGMG SS1932E | 7.5 m | eléctrica |
| LGMG S3246E II | 12.0 m | eléctrica |
| LGMG S4650EII | 15.8 m | eléctrica |

### Plataforma Unipersonal (1)
| Modelo | Altura trabajo | Alimentación |
|--------|----------------|--------------|
| LGMG MP1007SE | 12.1 m | eléctrica |

### Torre de Iluminación (1)
| Modelo | Tipo |
|--------|------|
| Trime X-SOLAR 4x65W | LED / Solar |

---

## 🆕 Productos SOLO en Inventario de Precios (13)

Estos productos existen en SQL pero **NO** tienen ficha técnica en `inventory_data.py`.

### Soldadora (2)
| Código | Producto | Precio USD | Stock |
|--------|----------|------------|-------|
| DGW400DMC | Soldadora DGW400DMC Shindaiwa | Sin precio | 1 |
| DGW600DM | Soldadora DGW600DM Shindaiwa | $38,691 | 1 |

### Plataforma Articulada (2)
| Código | Producto | Precio USD | Stock |
|--------|----------|------------|-------|
| AR52J-2 | Plataforma Articulada AR52J-2 LGMG | $75,844 | 2 |
| AR60J | Plataforma Articulada AR60J LGMG | $92,284 | 1 |

### Plataforma Articulada — posible relación (1)
| Código | Producto | Precio USD | Stock | Candidato técnico |
|--------|----------|------------|-------|-------------------|
| AR65JE | Plataforma Articulada AR65JE LGMG | $111,083 | 2 | ¿LGMG AR65JE-LI? |

### Plataforma de Mástil (1)
| Código | Producto | Precio USD | Stock |
|--------|----------|------------|-------|
| M2640JE | Plataforma de Mástil M2640JE LGMG | $22,419 | 2 |

### Plataforma Tijera (6)
| Código | Producto | Precio USD | Stock | Candidato técnico |
|--------|----------|------------|-------|-------------------|
| S1932E-2 | Plataforma Tijera S1932E-2 LGMG | Sin precio | 9 | ¿LGMG SS1932E? |
| S2632E-2 | Plataforma Tijera S2632E-2 LGMG | Sin precio | 5 | — |
| S2632EIILI | Plataforma Tijera S2632EIILI LGMG | Sin precio | 3 | — |
| S3246E-2 | Plataforma Tijera S3246E-2 LGMG | Sin precio | 15 | ¿LGMG S3246E II? |
| S4046E-2 | Plataforma Tijera S4046E-2 LGMG | Sin precio | 8 | — |
| S4650EIILI | Plataforma Tijera S4650EIILI LGMG | Sin precio | 7 | ¿LGMG S4650EII? |

### Torre de Iluminación (1)
| Código | Producto | Precio USD | Stock |
|--------|----------|------------|-------|
| SL430LIDG | Torre de Iluminación SL430LIDG Shindaiwa | Sin precio | 1 |

---

## 🔍 Observaciones Clave

### 1. Mapeos faltantes en `model_code_mapping.py`

Estas máquinas están claramente en ambos inventarios pero **no tienen entrada** en el archivo de mapeo. Se recomienda agregarlas:

| Modelo (técnico) | Código SQL | Acción |
|-------------------|-----------|--------|
| Shindaiwa DG100MI-400 | DG100MI400 | ➕ Agregar |
| Shindaiwa DGW500DM | DGW500DM200 | ➕ Agregar |
| Shindaiwa EGW185MS | EGW185MS | ➕ Agregar |
| LGMG MP0607SE | MP0607SE | ➕ Agregar |
| LGMG MP1208SE | MP1208SE | ➕ Agregar |

### 2. Mapeos con código inexistente en SQL

Estos mapeos en `model_code_mapping.py` apuntan a códigos que **no existen** en `prices_inventory.txt`:

| Modelo | Código mapeado | Estado |
|--------|---------------|--------|
| AIRMAN SDG150S | SDG150S3A6 | ❌ Código no encontrado en SQL |
| LGMG CPD30 | CPD30 | ❌ Código no encontrado en SQL |

### 3. Posibles relaciones sin confirmar

Máquinas de un inventario que podrían corresponder a productos del otro, pero necesitan verificación manual:

| Modelo técnico | Código SQL candidato | Notas |
|----------------|---------------------|-------|
| LGMG AR65JE-LI | AR65JE | Podría ser la versión Litio |
| LGMG S4650EII | S4650EIILI | Posible variante con Litio |
| LGMG SS1932E | S1932E-2 | Posible variante del modelo |
| LGMG S3246E II | S3246E-2 | Posible variante (sufijo II vs -2) |

### 4. Productos SQL sin precio asignado (8 de 50)

Estos productos están en SQL pero tienen `fixed_price: None`:
- `S1932E-2`, `S2632E-2`, `S2632EIILI`, `S3246E-2`, `S4046E-2`, `S4650EIILI`, `DGW400DMC`, `SL430LIDG`

### 5. Categorías exclusivas

| Categoría | Inventario |
|-----------|-----------|
| Plataforma de Mástil (M2640JE) | Solo en precios |
| Plataforma Unipersonal (MP1007SE) | Solo en técnico (MP0607SE y MP1208SE sí están en ambos) |

inventario = [
    # SOLDADORA
    {
        "modelo": "Shindaiwa DGW500DM", 
        "categoria": "soldadora", 
        "amperaje_amps_min": 30, 
        "amperaje_amps_max": 500, 
        "tipo_alimentacion": "diésel", 
        "tipo_soldadora": "moto soldadora", 
        "diametro_varilla": "3/8", 
        "tipo_trabajo": "electrodo"
    },
    {
        "modelo": "Shindaiwa EGW185MS", 
        "categoria": "soldadora", 
        "amperaje_amps_min": 45, 
        "amperaje_amps_max": 185, 
        "tipo_alimentacion": "gasolina", 
        "tipo_soldadora": "moto soldadora", 
        "diametro_varilla": "5/32", 
        "tipo_trabajo": "electrodo"
    },
    {
        "modelo": "Shindaiwa DGW400DMK", 
        "categoria": "soldadora", 
        "amperaje_amps_min": 50, 
        "amperaje_amps_max": 390, 
        "tipo_alimentacion": "diésel", 
        "tipo_soldadora": "moto soldadora", 
        "diametro_varilla": "5/16", 
        "tipo_trabajo": "electrodo, micro alambre, TIG, arcayeo"
    },
    {
        "modelo": "Shindaiwa DGW340DM", 
        "categoria": "soldadora", 
        "amperaje_amps_min": 55, 
        "amperaje_amps_max": 340, 
        "tipo_alimentacion": "diésel", 
        "tipo_soldadora": "moto soldadora", 
        "diametro_varilla": "5/16", 
        "tipo_trabajo": "electrodo, arcayeo"
    },

    # COMPRESORES
    {"modelo": "AIRMAN SAS75RD6E", "categoria": "compresor", "tipo_compresor": "estacionario/eléctrico", "caudal_cfm_max": 490.87, "presion_psi_max": 100},
    {"modelo": "AIRMAN SAS55RD6E", "categoria": "compresor", "tipo_compresor": "estacionario/eléctrico", "caudal_cfm_max": 360.21, "presion_psi_max": 100},
    {"modelo": "AIRMAN SAS37RD6E", "categoria": "compresor", "tipo_compresor": "estacionario/eléctrico", "caudal_cfm_max": 243.67, "presion_psi_max": 100},
    {"modelo": "AIRMAN SAS22RD6E", "categoria": "compresor", "tipo_compresor": "estacionario/eléctrico", "caudal_cfm_max": 144.79, "presion_psi_max": 100},
    {"modelo": "AIRMAN SAS8SD6C",  "categoria": "compresor", "tipo_compresor": "estacionario/eléctrico", "caudal_cfm_max": 35.31,  "presion_psi_max": 135},
    {"modelo": "AIRMAN SAS4SD6C",  "categoria": "compresor", "tipo_compresor": "estacionario/eléctrico", "caudal_cfm_max": 15.53,  "presion_psi_max": 120},
    {"modelo": "AIRMAN PDS830S",        "categoria": "compresor", "tipo_compresor": "portátil", "caudal_cfm_max": 830, "presion_psi_max": 150},
    {"modelo": "AIRMAN PDSG750VRS-4C5", "categoria": "compresor", "tipo_compresor": "portátil", "caudal_cfm_max": 900, "presion_psi_max": 100},
    {"modelo": "AIRMAN PDS750S-4B1",    "categoria": "compresor", "tipo_compresor": "portátil", "caudal_cfm_max": 750, "presion_psi_max": 100},
    {"modelo": "AIRMAN PDSF375S-DP",    "categoria": "compresor", "tipo_compresor": "portátil", "caudal_cfm_max": 375, "presion_psi_max": 100},
    {"modelo": "AIRMAN PDS185S-6C2",    "categoria": "compresor", "tipo_compresor": "portátil", "caudal_cfm_max": 185, "presion_psi_max": 100},

    # ROMPEDOR, AKA Martillo
    {"modelo": "Toku TCB-300", "categoria": "rompedor", "tipo_alimentacion": "neumatico", "peso_kg": 30},
    {"modelo": "Toku TPB-90",  "categoria": "rompedor", "tipo_alimentacion": "neumatico", "peso_kg": 42},

    # MOTOBOMBAS
    {"modelo": "Koshin KTY-100D", "categoria": "motobomba", "diametro_salida_pulgadas": 4, "tipo_combustible": "diésel"},
    {"modelo": "Koshin KTH-100 X", "categoria": "motobomba", "diametro_salida_pulgadas": 4, "tipo_combustible": "gasolina"},

    # APISONADOR
    {"modelo": "Sakai RS75", "categoria": "apisonador", "motor": "Honda GXR120", "ancho_zapata_mm": 395},

    # GENERADOR
    # Nota: Conversiones aproximadas kVA -> kW (0.8 PF)
    {"modelo": "Shindaiwa DGM250MK-D", "categoria": "generador", "tipo_generador": "estacionario", "potencia_kva": 25, "potencia_kw": 20.0, "tipo_alimentacion": "diésel"},
    {"modelo": "Shindaiwa DGM450MK-D", "categoria": "generador", "tipo_generador": "estacionario", "potencia_kva": 45, "potencia_kw": 36.0, "tipo_alimentacion": "diésel"},
    {"modelo": "Shindaiwa DGM600MK-D", "categoria": "generador", "tipo_generador": "estacionario", "potencia_kva": 60, "potencia_kw": 48.0, "tipo_alimentacion": "diésel"},
    {"modelo": "Shindaiwa DG100MI-400", "categoria": "generador", "tipo_generador": "estacionario", "potencia_kva": 100, "potencia_kw": 80.0, "tipo_alimentacion": "diésel"},
    {"modelo": "Koshin GV-8000S",     "categoria": "generador", "tipo_generador": "portátil",     "potencia_kva": 9.0, "potencia_kw": 7.2,   "tipo_alimentacion": "gasolina"}, # 7.2kW / 0.8 = 9kVA approx
    {"modelo": "Koshin GV-5500s",     "categoria": "generador", "tipo_generador": "portátil",     "potencia_kva": 5.5, "potencia_kw": 4.4,   "tipo_alimentacion": "gasolina"},

    # CORTADOR DE VARILLAS
    {"modelo": "Simpedil C54 EVO", "categoria": "cortadora_varillas", "tipo_alimentacion": "Eléctrica", "diametro_maximo_varilla_pulgadas": 1.75, "cortes_por_minuto": 37},

    # DOBLADOR DE VARILLAS
    {"modelo": "Simpedil P54 EVO", "categoria": "dobladora_varillas", "tipo_alimentacion": "Eléctrica", "diametro_maximo_varilla_pulgadas": 1.75, "cortes_por_minuto": 6},

    # TORRE DE ILUMINACIÓN
    {
      "modelo": "Shindaiwa SL433IDG-B/S1W",
      "categoria": "torre_iluminacion",
      "tipo_reflector": "LED",
      "consumo_combustible_litros": 15.5,
      "remolcable": "Sí"
    },
    {
      "modelo": "Trime X-START",
      "categoria": "torre_iluminacion",
      "tipo_reflector": "LED",
      "consumo_combustible_litros_hora": 0.55,
      "remolcable": "Sí"
    },

    # MONTACARCAS
    {"modelo": "Noblelift CPQYD30", "categoria": "montacargas", "tipo_combustible": "Gas o Gasolina", "capacidad_toneladas": 3},
    {"modelo": "Noblelift CPCD30", "categoria": "montacargas", "tipo_combustible": "Diésel", "capacidad_toneladas": 3},
    {"modelo": "Noblelift FE4P25Q", "categoria": "montacargas", "tipo_combustible": "Eléctrico", "capacidad_toneladas": 2.5},

    # PLATAFORMAS
    {"modelo": "LGMG AR60JE-2", "categoria": "plataforma", "tipo_plataforma": "articulada", "altura_trabajo_m": 20.12, "altura_plataforma_m": 18.12, "tipo_alimentacion": "electrica"},
    {"modelo": "LGMG AR60J-2",  "categoria": "plataforma", "tipo_plataforma": "articulada", "altura_trabajo_m": 20.12, "altura_plataforma_m": 18.12, "tipo_alimentacion": "combustible"},
    {"modelo": "LGMG AR52J",    "categoria": "plataforma", "tipo_plataforma": "articulada", "altura_trabajo_m": 17.70, "altura_plataforma_m": 15.7,  "tipo_alimentacion": "combustible"},
    {"modelo": "LGMG A45JE-LI", "categoria": "plataforma", "tipo_plataforma": "articulada", "altura_trabajo_m": 16.09, "altura_plataforma_m": 14.09, "tipo_alimentacion": "electrica"},
    {"modelo": "LGMG SS1230E",  "categoria": "plataforma", "tipo_plataforma": "tijera",     "altura_trabajo_m": 5.6,   "altura_plataforma_m": 3.6,   "tipo_alimentacion": "electrica"},
    {"modelo": "LGMG S2632E II", "categoria": "plataforma", "tipo_plataforma": "tijera",     "altura_trabajo_m": 10,    "altura_plataforma_m": 8,     "tipo_alimentacion": "electrica"},
    {"modelo": "LGMG S4046E II", "categoria": "plataforma", "tipo_plataforma": "tijera",     "altura_trabajo_m": 14,    "altura_plataforma_m": 12,    "tipo_alimentacion": "electrica"},
    {"modelo": "LGMG S1932EII", "categoria": "plataforma", "tipo_plataforma": "tijera", "altura_trabajo_m": 7.5, "altura_plataforma_m": 5.8, "tipo_alimentacion": "electrica"},

    {"modelo": "LGMG S3246E II", "categoria": "plataforma", "tipo_plataforma": "tijera", "altura_trabajo_m": 12, "altura_plataforma_m": 10, "tipo_alimentacion": "electrica"},
    {"modelo": "LGMG MP0607SE", "categoria": "plataforma", "tipo_plataforma": "unipersonal", "altura_trabajo_m": 8.2, "altura_plataforma_m": 6.2, "tipo_alimentacion": "electrica"},
    {"modelo": "LGMG MP1007SE", "categoria": "plataforma", "tipo_plataforma": "unipersonal", "altura_trabajo_m": 12.1, "altura_plataforma_m": 10.1, "tipo_alimentacion": "electrica"},
    {"modelo": "LGMG MP1208SE", "categoria": "plataforma", "tipo_plataforma": "unipersonal", "altura_trabajo_m": 14, "altura_plataforma_m": 12, "tipo_alimentacion": "electrica"},
    {"modelo": "LGMG M2640JE", "categoria": "plataforma", "tipo_plataforma": "mástil", "altura_trabajo_m": 10, "altura_plataforma_m": 8, "tipo_alimentacion": "electrica"},

    # MANIPULADORES
    {"modelo": "LGMG H1840", "categoria": "manipulador", "altura_maxima_m": 17.5, "capacidad_toneladas": 4},
]
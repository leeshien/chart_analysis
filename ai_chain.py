"""The AI industrial chain as tiers of US-listed tickers.

Grouped by position in the chain rather than by GICS, because the correlation
evidence showed GICS boundaries do not track co-movement in this space: the
Systems/Application Software split measured 0.42 across the boundary against
0.39 within one side of it, while data-centre electrical names measured -0.13
against regulated utilities despite both sitting under "power".

Membership here is knowledge-derived, not scraped, so it must be validated
before use — see validate_ai_chain.py.
"""

CHAIN = {
    "1a_eda_ip": ["CDNS", "SNPS", "ARM"],

    "2a_litho": ["ASML"],
    "2b_deposition_etch": ["AMAT", "LRCX", "ACMR", "UCTT", "ICHR", "AEIS",
                           "MKSI", "VECO"],
    "2c_metrology_inspection": ["KLAC", "ONTO", "NVMI", "CAMT", "PLAB"],
    "2d_test": ["TER", "FORM", "COHU"],
    "2e_packaging": ["KLIC", "AMKR", "ASX"],
    "2f_materials": ["Q", "AZTA", "AXTI"],

    "3a_datacentre_compute": ["NVDA", "AMD"],
    "3b_custom_asic_interconnect": ["AVGO", "MRVL", "ALAB", "CRDO", "RMBS",
                                    "LSCC", "SITM"],
    "3c_server_power": ["MPWR", "NVTS", "POWI", "AOSL"],
    "3d_analog_auto": ["TXN", "ADI", "NXPI", "MCHP", "ON", "STM", "ALGM",
                       "SLAB", "SYNA", "DIOD", "VSH", "MXL", "WOLF"],
    "3e_wireless_rf": ["QCOM", "SWKS", "QRVO", "MTSI"],

    "4a_foundry": ["INTC", "TSM", "GFS", "UMC", "TSEM"],
    "4b_memory": ["MU"],
    "4c_nand_hdd": ["SNDK", "WDC", "STX"],

    "5a_switching": ["ANET", "CSCO", "EXTR", "UI"],
    "5b_optical_transport": ["CIEN", "RBBN"],
    "5c_optical_components": ["LITE", "COHR", "FN", "AAOI", "AXTI"],
    "5d_fibre_passive": ["GLW", "BDC"],
    "5e_connectors_copper": ["APH", "TEL", "LFUS", "BELFB"],

    "6a_power_distribution": ["VRT", "ETN", "HUBB", "POWL", "NVT", "ATKR", "ENS"],
    "6b_automation": ["EMR", "ROK", "AME"],
    "6c_cooling_thermal": ["MOD", "AAON", "SPXC"],
    "6d_generation_equipment": ["GEV", "GNRC", "BE"],
    "6e_grid_epc": ["PWR", "MYRG", "IESC", "DY", "MTZ", "PRIM"],

    "7a_merchant_power": ["CEG", "NRG", "AES", "VST", "TLN"],
    "7b_nuclear_fuel": ["BWXT", "LEU", "SMR", "OKLO", "CCJ", "UEC"],
    "7c_regulated_utilities": ["D", "DUK", "AEP", "EIX", "ETR", "DTE", "ED",
                               "AEE", "CMS", "LNT", "CNP", "IDA", "POR",
                               "OGE", "BKH", "AVA", "NWE"],

    "8a_servers_storage": ["SMCI", "DELL", "HPE", "NTAP"],
    "8b_contract_manufacture": ["JBL", "FLEX", "CLS", "SANM", "PLXS", "BHE"],
    "8c_datacentre_reits": ["EQIX", "DLR"],
    "8d_neocloud": ["CRWV", "NBIS", "APLD", "IREN", "CORZ", "WULF", "CIFR", "HUT"],
    "8e_construction": ["FIX", "EME", "J", "STRL", "APG", "GVA", "ROAD"],

    "9a_hyperscale": ["MSFT", "AMZN", "GOOGL", "META", "ORCL"],
    "9b_data_platform": ["SNOW", "MDB", "ESTC", "GTLB"],
    "9c_edge_delivery": ["AKAM", "NET", "FSLY", "DOCN"],
    "9d_ai_native_app": ["PLTR", "AI", "TEM", "RXRX", "SOUN", "BBAI", "PATH"],
    "9e_security": ["CRWD", "PANW", "FTNT", "GEN", "ZS", "S", "OKTA",
                    "QLYS", "TENB"],
    "9f_deployment_services": ["ACN", "IBM", "CTSH", "IT", "LDOS", "EPAM",
                               "GLOB", "INFY", "WIT", "DXC", "KD", "CACI",
                               "SAIC", "BAH"],
    # The application layer was described in the chain analysis and then left
    # out of this map, which dropped 97 IT alerts into "(no chain tier)".
    # Included now for coverage, with a caveat: Application Software measured
    # only 0.39 internal correlation, so these are weak groups by co-movement
    # and a "cluster" among them means less than one in security or analog.
    "9g_system_of_record": ["CRM", "NOW", "WDAY", "INTU", "TYL", "FICO"],
    "9h_design_engineering_tools": ["ADBE", "ADSK", "PTC", "TRMB"],
    "9i_observability": ["DDOG"],
    "9j_instruments": ["KEYS", "TDY", "ZBRA", "ROP"],
}


def all_tickers() -> list:
    """Every ticker once, sorted."""
    return sorted({t for names in CHAIN.values() for t in names})


def tier_of(ticker) -> list:
    """Which tiers a ticker belongs to. Several names sit in more than one."""
    return sorted(k for k, v in CHAIN.items() if ticker in v)

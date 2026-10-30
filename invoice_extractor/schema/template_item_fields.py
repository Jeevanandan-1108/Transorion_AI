from __future__ import annotations

ITEM_FIELDS_BY_TEMPLATE_FILE: dict[str, dict[str, str | None]] = {
    "ExportXLSXTemplate": {
        "ser_no": "ItemSrNo", "description": "Product_Desc",
        "quantity": "Quantity", "unit": "Quantity_Unit_Code",
        "unit_price": "Unit_Price", "line_amount": None,
        "per_unit": "Per_Unit_No.", "rate_unit": None,
        "taxable_value": "IGST_Taxable_Amt",
        "igst_amount": "IGST_Amount", "igst_rate": "IGST_Rate",
        "tariff_qty": None, "tariff_unit": None,
        "country_of_origin": None, "hsn": "RITC",
        "state_code": "SourceState", "total_packages": None,
    },
    "iCaffeExcel_Export": {
        "ser_no": None, "description": "Goods Description1",
        "quantity": "Quantity", "unit": "Unit of Qty",
        "unit_price": "Rate Value", "line_amount": None,
        "per_unit": "No of Unit (Per)", "rate_unit": "Unit of Rate",
        "taxable_value": "TAXABLE_VALUE",
        "igst_amount": "IGST_AMOUNT", "igst_rate": "IGST_RATE",
        "tariff_qty": None, "tariff_unit": None,
        "country_of_origin": None, "hsn": "Ritc",
        "state_code": "SOURCE_STATE", "total_packages": "TOTAL_PKG",
    },
    "Import- LOGYSIS TEMPLATE": {
        "ser_no": None, "description": "Product Desc",
        "quantity": "Quantity", "unit": "Unit",
        "unit_price": "Rate", "line_amount": "Amount",
        "per_unit": None, "rate_unit": None,
        "taxable_value": None, "igst_amount": None, "igst_rate": None,
        "tariff_qty": None, "tariff_unit": None,
        "country_of_origin": "Country of origin", "hsn": "CTH",
        "state_code": None, "total_packages": None,
    },
    "ImportExcel (3)": {
        "ser_no": None, "description": "ITEM_DESCRIPTION",
        "quantity": "QTY", "unit": "UQC",
        "unit_price": "UNIT_PRICE", "line_amount": "TOTAL",
        "per_unit": None, "rate_unit": None,
        "taxable_value": None, "igst_amount": None, "igst_rate": None,
        "tariff_qty": None, "tariff_unit": None,
        "country_of_origin": "ORIGIN_COUNTRY", "hsn": "RITC",
        "state_code": None, "total_packages": None,
    },
}

EXPORT_TEMPLATE_FILES = {"ExportXLSXTemplate", "iCaffeExcel_Export"}

GENERIC_KEY_TO_CONCEPT: dict[str, str] = {
    "item_ser_no": "ser_no",
    "product_description": "description",
    "quantity": "quantity",
    "unit_of_measurement": "unit",
    "unit_price": "unit_price",
    "line_total": "line_amount",
    "hsn_code": "hsn",
    "country_of_origin": "country_of_origin",
    "packages": "total_packages",
}


def template_file_stem(shipment_type: str) -> str | None:
    from invoice_extractor.schema.templates_config import TEMPLATE_PATH
    path = TEMPLATE_PATH.get(shipment_type)
    return path.stem if path else None


def item_fields_for_template(shipment_type: str) -> dict[str, str | None]:
    return ITEM_FIELDS_BY_TEMPLATE_FILE.get(template_file_stem(shipment_type) or "", {})


def is_export_template(shipment_type: str) -> bool:
    return shipment_type == "export" or template_file_stem(shipment_type) in EXPORT_TEMPLATE_FILES

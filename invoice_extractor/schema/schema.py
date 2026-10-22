#heloo  
#bello
#suneo
"""
schema.py
---------
Builds a JSON schema (per sheet) directly from the column headers of the
customs export/import template (.xlsx), so the extractor never needs the
column names hardcoded.

Honest caveat: the column *headers themselves* are read dynamically from
whichever template you point at, but the aliases and type-forcing below
(_MANUAL_ALIASES, _STRING_FORCE) are hand-curated India-customs domain
knowledge tied to specific column names (e.g. "FTA Code" is always a code,
never a number; "Item_RITC" is commonly called "HSN Code" on invoices).
That part is intentionally hardcoded and has to stay that way -- customs
data needs it. If you rename a column in the template, the schema will
still pick up the new header automatically, but any alias/force-type rule
written against the *old* name will silently stop applying to it. There's
no automatic detection of that today; re-check this file's lists after
renaming columns.

Also generates human-readable aliases for each column name, so the LLM
prompt can show the model what common invoice labels map to which schema
field.  For example:  Item_Country_Org  ->  "Country of Origin".
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

import openpyxl

# Heuristics for inferring a field's data type purely from its column name.
_NUMBER_HINTS = ("qty", "price", "amt", "amount", "percentage", "value",
                  "val", "rate", "duty", "pmv", "pkg", "taxable")
_DATE_HINTS = ("date",)

# Fields that are ALWAYS strings despite potentially matching number hints.
# Y/N flags, codes, text fields, etc.
_STRING_FORCE = {
    "item_cess", "item_accessory", "item_thirdparty", "item_quota",
    "item_ar4", "item_reward", "item_str", "item_jnoti_no",
    "per", "printed", "itm_source_cntry", "itm_transit_cntry",
    "itmigststatus", "item_end_use", "fta code", "state code",
    "district code", "cess per", "cess rate", "cess amount",
    "item_scheme_code", "itm_manu_codetype", "accessory_status",
    "item_ser_no", "sl_no",
}

# No system fields excluded — all fields including "Printed" are real.
_SYSTEM_FIELDS: set[str] = set()

# ── Manual, hand-written explanations for cryptic customs fields ──
# Unlike _MANUAL_ALIASES (alternate LABELS the LLM might see printed on the
# document), this is what the field actually MEANS -- for abbreviations and
# notification/scheme concepts terse enough that even a good alias list
# doesn't explain them (e.g. "CTH" by itself tells the model nothing; "CTH:
# Customs Tariff Heading -- the HS/tariff classification code for this
# item" does). Combined with that field's own aliases into one description
# string -- see _build_sheet_entry below and chunked_extraction.py's
# _row_model_for_chunk/_build_chunk_prompt (the consumers that actually put
# this in front of the LLM). Optional: a field with no entry here just gets
# its alias list alone, same as before this existed.
#
# This dict is keyed by column name ALONE, shared across every template
# that happens to reuse that name -- fine when the meaning is genuinely the
# same everywhere (most cases), but wrong for a name reused with a
# GENUINELY DIFFERENT meaning per sheet (confirmed real case: "Quantity" on
# logi's own ITEMS sheet means this line item's quantity; "Quantity" on
# import_excel_3's Antidumping sheet means the quantity subject to
# anti-dumping duty -- two different concepts that happen to share a column
# name). _MANUAL_DESCRIPTIONS_BY_SHEET below is the fix: checked FIRST, by
# (sheet_name, field_name), before this dict's name-only fallback -- see
# _build_sheet_entry.
#
# DRAFT, not yet domain-confirmed: the "Import- LOGYSIS TEMPLATE.csv" block
# below is a first pass written from general Indian customs/BOE (Bill of
# Entry) filing knowledge, NOT from a confirmed real invoice/filing case the
# way the rest of this dict's entries are (see that convention noted at
# _MANUAL_ALIASES below) -- please review before relying on it, especially
# "TOI" (flagged inline: genuinely ambiguous without more context) and the
# three duplicate "Sr no" columns (same literal header used after Basic/
# Excise/SAD notification -- these need renaming in the source template
# before they can be reliably distinguished at all; see the conversation
# that produced this comment).
_MANUAL_DESCRIPTIONS: dict[str, str] = {}
_MANUAL_DESCRIPTIONS.update({
    # -- Generic fallback, shared by any template -- see
    # _MANUAL_DESCRIPTIONS_BY_SHEET for this name's own sheet-specific
    # meanings (JOBWORK, Antidumping), which take precedence over this.
    "Notification_No": "Notification number referenced by this row (see the specific sheet for which scheme/duty it applies to).",

    # -- Import- LOGYSIS TEMPLATE.csv ("logysis_import") --
    "Inv No": "The supplier's own commercial invoice number for this shipment.",
    "Date": "The commercial invoice's own date.",
    "TOI": (
        "UNCONFIRMED MEANING -- likely \"Terms of Invoice\" (e.g. an Incoterm such as "
        "FOB/CIF/EXW) or possibly \"Type of Invoice\"; verify against a real filed BOE "
        "before trusting this."
    ),
    "Product Desc": "The commercial description of the goods for this specific line item.",
    "Quantity": "The quantity of this line item.",
    "Unit": "The unit of measure (UQC -- Unit Quantity Code) this item's quantity is stated in.",
    "Currency": "The invoice currency this item's rate/amount is stated in.",
    "Rate": "The unit price (price per single unit of Quantity) for this item.",
    "CTH": "Customs Tariff Heading -- the HS/ITC-HS tariff classification code for this item.",
    "Basic notification": "Basic Customs Duty (BCD) exemption/concession notification number claimed for this item.",
    "CETH": (
        "Central Excise Tariff Heading -- the classification code under the Central Excise "
        "Tariff (legacy/pre-GST, still relevant for excise-levied goods like petroleum and tobacco)."
    ),
    "Excise notification": "Central Excise duty exemption/concession notification number claimed.",
    "Mrp Sr no": (
        "Serial number within the MRP (Maximum Retail Price)-based valuation notification's own "
        "list of entries, for goods assessed on MRP rather than transaction value."
    ),
    "Amount": "The total line amount for this item (Quantity x Rate).",
    "SAD notification": (
        "Special Additional Duty (SAD) exemption notification number claimed -- a legacy duty "
        "largely subsumed into GST/IGST post-2017, but the field persists in some BOE formats."
    ),
    "Exim Code": "Export-Import (EXIM) policy code, sometimes used alongside/instead of the IEC.",
    "Scheme Notn": (
        "Scheme notification number -- e.g. the EPCG/Advance Authorization/DFIA export-promotion "
        "scheme notification under which this item is being cleared."
    ),
    "Scheme SrNo": "Serial number within that scheme notification's own list of entries.",
    "Generic Description": (
        "A generic/tariff-level description of the goods, as opposed to the specific commercial "
        "description in Product Desc -- used for customs classification purposes."
    ),
    "BRAND": "Brand name of the goods.",
    "Model": "Model number or variant of the goods.",
    "END USE": "End-use code/declaration, where a concessional duty rate depends on the goods' declared end use.",
    "EduCessNotnNo": (
        "Education Cess notification number (legacy surcharge on customs duty, phased out "
        "around 2015 -- the field persists in some BOE formats)."
    ),
    "EduCessNotnSrNo": "Serial number within that Education Cess notification.",
    "SHEduCessNotnNo": "Secondary and Higher Education Cess notification number (a second tier of the legacy cess above).",
    "SHEduCessNotnSrNo": "Serial number within that Secondary and Higher Education Cess notification.",
    "CEXEduCessNotnNo": "Central Excise Education Cess notification number.",
    "CEXEduCessNotnSrNo": "Serial number within that Central Excise Education Cess notification.",
    "Country of origin": "The country where the goods were manufactured/produced.",
    "RSP Notf": (
        "Retail Sale Price notification number, for goods assessed on MRP under the Legal "
        "Metrology (Packaged Commodities) framework."
    ),
    "Sapta Notif No": (
        "SAPTA (South Asian Preferential Trade Arrangement) preferential tariff notification "
        "number claimed."
    ),
    "SAPTA Sr No": "Serial number within that SAPTA notification.",
    "Manufacturer": "Name of the actual manufacturer of the goods (may differ from the supplier/seller on the invoice).",
    "Address": "The manufacturer's address.",
    "MRP Status": "Y/N flag -- whether this item is valued using MRP-based (Maximum Retail Price) assessment.",
    "Abatement Rate": "The abatement percentage allowed on MRP when computing this item's assessable value.",
    "IGST Notn No": "Integrated GST (IGST) exemption/concession notification number claimed on import.",
    "IGST SrNo": "Serial number within that IGST notification.",
    "GST CCESS Notn No": (
        "GST Compensation Cess notification number claimed -- applies to luxury/sin goods such "
        "as tobacco, coal, and automobiles."
    ),
    "GST CCESS SrNo": "Serial number within that GST Compensation Cess notification.",
    "IGST Exemp Notn Type": "Type/category of the IGST exemption notification claimed for this item.",
    "IGST Exemp Notn No": "IGST exemption notification number (as distinct from a concession under IGST Notn No).",
    "IGST Exemp SrNo": "Serial number within that IGST exemption notification.",
    "GST CCESS Exemp NotnType": "Type/category of the GST Compensation Cess exemption notification claimed.",
    "GST CCESS Exemp Notn No": "GST Compensation Cess exemption notification number.",
    "GST CCESS Exemp SrNo": "Serial number within that GST Compensation Cess exemption notification.",
    "Manufacturer Cntry": "Country of the manufacturer.",
    "Manufacturer State": "State/province of the manufacturer, within the manufacturer's own country.",
    "Manuf Postal Code": "The manufacturer's postal/ZIP code.",
    "SWS Notn No": "Social Welfare Surcharge (SWS) exemption notification number claimed.",
    "SWS Notn SrNo": "Serial number within that Social Welfare Surcharge notification.",
    "Duty Exemption Type": "The category/type of duty exemption claimed for this item overall.",
    "Regn No.": "Registration number of the import license/authorization covering this item.",
    "Regn Date": "Date that registration/license was registered.",
    "Regn Port": "The port at which that registration/license was registered.",
    "Item SNo. In Lic.": "This item's own serial number as listed within the import license/authorization document.",
    "Product Material Code": "An internal or customs material/commodity code for the product.",
    "Type Of Item": "Category/type classification of the item.",
    "Source Country": "Country the goods were shipped/sourced from (may differ from Country of origin if transshipped).",
    "Transit Country": "Country through which the goods transited en route to India.",
    "SQC_Qty": (
        "Statistical Quantity Code quantity -- the quantity expressed in the standard unit used "
        "for customs trade statistics, distinct from the commercial Quantity/Unit above."
    ),
    "Consigner Name": "Name of the consignor -- the party shipping the goods (often the exporter/manufacturer).",
    "Consigner Address": "The consignor's address.",
    "Consigner City": "The consignor's city.",
    "Consigner Country": "The consignor's country.",
    "SIMS Category": "Steel Import Monitoring System (SIMS) category code, applicable to steel product imports.",
    "SIMS Code": "SIMS registration code/number for this import.",
    "Custom Health Cess Notn No": (
        "Health Cess notification number -- Health Cess is levied on certain medical device imports."
    ),
    "Custom Health Cess Notn SrNo": "Serial number within that Health Cess notification.",
    "COO_No": "Certificate of Origin (COO) number.",
    "COO_Date of Issue": "Date the Certificate of Origin was issued.",
    "COO_Issuing Country": "The country that issued the Certificate of Origin.",
    "COO_Origin Criteria": (
        "The origin criteria code under which the goods qualify as originating under the relevant "
        "trade agreement (e.g. wholly obtained, change in tariff heading, regional value content)."
    ),
    "COO_Origin Criteria remarks": "Additional remarks/explanation on the origin criteria claimed.",
    "COO_Accumulation/Cumulation Status": (
        "Whether cumulation/accumulation provisions (combining originating materials from multiple "
        "FTA partner countries) were applied in determining origin."
    ),
    "FOC Item": "Y/N flag -- Free of Cost item (e.g. a warranty replacement or free sample supplied without charge).",
    "AIDC Levy Notification No": "Agriculture Infrastructure and Development Cess (AIDC) levy notification number.",
    "AIDC Levy Notification Sr.No.": "Serial number within that AIDC levy notification.",
    "AIDC Exemption Notification No.": "AIDC exemption notification number claimed.",
    "AIDC Exemption Notification Sr.No.": "Serial number within that AIDC exemption notification.",
    "AIDCNotn_Excise": (
        "AIDC notification number applicable under the Central Excise regime (e.g. for petroleum "
        "products, where AIDC is levied via excise rather than customs)."
    ),
    "ADICNotnSr_Excise": "Serial number within that excise-side AIDC notification.",
    "Previous BE No.": "Bill of Entry number of a prior, related import (e.g. a linked earlier consignment or re-import).",
    "Previous BE Date": "Date of that previous Bill of Entry.",
    "Previous BE IGM No.": "Import General Manifest (IGM) number associated with that previous Bill of Entry.",
    "Previous BE IGM Date": "Date of that previous Import General Manifest.",
    "Previous BE Currency": "The invoice currency recorded on that previous Bill of Entry.",
    "Previous BE Unit Price": "The unit price recorded on that previous Bill of Entry (for comparison, e.g. in re-import cases).",
    "Previous BE Custom House": "The customs house (port/ICD) where that previous Bill of Entry was filed.",
    "Aggregate Duty Notification No.": "Notification number under which an aggregate (combined) duty rate was claimed for this item.",
    "Aggregate Duty Notification Sr.No.": "Serial number within that aggregate duty notification.",
    "COO_Tariff Shift": (
        "Whether origin was determined via a \"tariff shift\" (change in tariff classification) "
        "rule-of-origin method."
    ),
    "COO_Retroactive Issuance Status": (
        "Whether the Certificate of Origin was issued retroactively (after the goods were shipped) "
        "rather than at the time of shipment."
    ),
    "COO_Direct Consignment Status": (
        "Whether the goods meet the \"direct consignment\" rule of origin (shipped directly from "
        "the originating country without further processing elsewhere en route)."
    ),
    "GST_Comp_Cess_SalePrice_INR": (
        "The sale price in INR used as the basis for computing GST Compensation Cess, for goods "
        "(e.g. tobacco) where the cess is based on retail sale price rather than assessable value."
    ),
    "CVD_Notn": "Countervailing Duty (CVD) notification number claimed (anti-subsidy duty on this item).",
    "CVD_SLNo": "Serial number within that CVD notification.",
    "CVD_Rate": "The Countervailing Duty rate (percentage or amount) applicable to this item.",
    "CVD_Calc": "The calculated Countervailing Duty amount for this item.",
    "CVD_ItemSLNo": "This item's own serial number within the CVD notification/order's item list.",
    "CVD_SuppSLNo": (
        "The supplier's serial number within the CVD notification/order -- anti-dumping/CVD "
        "orders often list specific supplier-wise rates."
    ),
    "COO_ItemSrNoCert": "This item's own serial number as listed on the Certificate of Origin certificate.",
    # The three columns below share the EXACT SAME literal header "Sr no" in
    # the source .csv (after Basic notification, Excise notification, and
    # SAD notification respectively) -- see this dict's own module-level
    # comment. Python/Pydantic cannot carry three different descriptions
    # under one identical key, so this single entry has to describe all
    # three ambiguously until the source columns are renamed to be distinct.
    "Sr no": (
        "AMBIGUOUS -- this exact column header is reused 3 times in the source template (after "
        "Basic notification, Excise notification, and SAD notification), each meaning THAT "
        "notification's own serial number. They cannot be told apart or filled independently "
        "until the source template's columns are renamed to be distinct; see field_naming "
        "discussion. Until then, treat any value here as unreliable."
    ),
    "Sr. no.": (
        "Serial number within the RSP Notf (Retail Sale Price notification) list -- a separate, "
        "near-duplicate header (with periods) from the \"Sr no\" columns above; not ambiguous on "
        "its own, but easy to confuse with them."
    ),
})

# DRAFT, not yet domain-confirmed (same caveat as the logi block above): the
# "ExportXLSXTemplate.xlsx" ("export_xlsx") block below is a first pass from
# general knowledge of India's ICEGATE Shipping Bill (EDI) filing format --
# SB_Type, DEEC/EPCG/DFIA/DFRC license schemes, RoDTEP, e-Sanchit, and the
# Single Window (SW_*) PGA declaration sheets are all standard concepts in
# that format, but exact field-level meaning should be verified against a
# real filed Shipping Bill before fully trusting it. "LGD_Code" (ITEMS) is
# flagged inline as genuinely uncertain. Several sheets (DEEC/EPCG/DFIA)
# share an identical field set (a standard license-utilization block
# repeated per scheme) -- their descriptions below are written once per
# field name, applying to whichever of those sheets uses it, since
# _MANUAL_DESCRIPTIONS is keyed by field name only, not by (sheet, field).
_MANUAL_DESCRIPTIONS.update({
    # -- ExportXLSXTemplate.xlsx ("export_xlsx"): GENERAL --
    "TransportMode": "Mode of transport for this shipment (e.g. Sea, Air, Road, Rail, ICD).",
    "CustomHouseCode": "The customs house/port code where this Shipping Bill is filed.",
    "SB_Type": "Shipping Bill type/category (e.g. Free Shipping Bill, Drawback, DEEC, EPCG, EOU, Coastal).",
    "Is_SEZ_Job": "Y/N flag -- whether this shipment relates to an SEZ (Special Economic Zone) job-work transaction.",
    "Loading_Port": "The port where the goods are loaded onto the vessel/aircraft.",
    "Exporter_Name": "Name of the exporter.",
    "BranchName": "The exporter's branch name/code filing this shipment.",
    "ExporterRefNo": "The exporter's own internal reference number for this shipment.",
    "ExporterRefDate": "Date of that exporter reference.",
    "ExporterType": "Category of exporter (e.g. Manufacturer, Merchant, EOU, SEZ unit).",
    "IsGRWaived": "Y/N flag -- whether the GR (Guaranteed Remittance) waiver applies, exempting this shipment from RBI foreign exchange declaration.",
    "GR_RBIWaiver_No": "The RBI GR waiver reference number, when claimed.",
    "GR_RBIWaiver_date": "Date of that RBI GR waiver.",
    "AD_Code": "Authorized Dealer (bank) code through which export proceeds will be realized.",
    "Consignee_name": "Name of the consignee (the overseas receiving party).",
    "Consignee_address": "The consignee's address.",
    "Consignee_Country_Code": "The consignee's country code.",
    "IsBuyerOtherThanConsignee": "Y/N flag -- whether the buyer is a different party than the consignee.",
    "Notify_name": "Name of the notify party (to be notified on arrival, per the shipping documents).",
    "Notify_address": "The notify party's address.",
    "Buyers_Order_No": "The buyer's purchase order number.",
    "Buyers_Order_Date": "Date of that buyer's purchase order.",
    "Other_Ref_No": "Any other reference number recorded for this shipment.",
    "Terms_of_Delivery_&_Payment": "The Incoterm and payment terms agreed for this shipment (e.g. FOB/CIF, Letter of Credit, Advance).",
    "Origin_Country_Code": "Country code of origin of the goods.",
    "Type_of_Shipment": "Category of shipment (e.g. Outright Sale, Sample, Gift, Export on Lease, Replacement).",
    "Specify_if_Other_Type_of_Shipment": "Free-text description used when Type_of_Shipment is \"Other\".",
    "Permission_No": "Permission/license number authorizing this export, if applicable.",
    "Permission_Date": "Date of that permission.",
    "Export_Under": "The scheme/claim under which the goods are exported (e.g. Drawback, DEEC, EPCG, Free Shipping Bill).",
    "Discharge_Port_Code": "Port code where the goods will be discharged/unloaded.",
    "Discharge_Country_Code": "Country code of the discharge port.",
    "Destination_Port_Code": "Final destination port code.",
    "Destination_Country_Code": "Final destination country code.",
    "Nature_of_Cargo": "Category of cargo (e.g. Containerized, Breakbulk, Bulk, LCL).",
    "Total_Packages": "Total number of packages in the shipment.",
    "Package_Unit_Code": "Unit code for the package count (e.g. Cartons, Boxes, Pallets).",
    "Loose_Packets": "Count of any loose (unpacked) packets in the shipment.",
    "Gross_Weight": "Total gross weight of the shipment.",
    "Net_Weight": "Total net weight of the shipment.",
    "Weight_Unit_Code": "Unit of weight used (e.g. KGS, TON).",
    "Origin_State_Code": "State code within India where the goods originated.",
    "Marks_&_No.": "Shipping marks and numbers printed on the packages.",
    "Warehouse_Code": "Bonded warehouse code, if the goods are exported ex-bond.",

    # -- SEZ_INFO --
    "Mode_of_Transport": "Mode of transport for this shipment.",
    "Purpose_of_Transaction": "The commercial purpose of this export transaction.",
    "Mode_of_Realisation": "How export proceeds will be realized (e.g. Advance payment, Letter of Credit, DA/DP).",
    "Bank_for_Receiving_Payment": "Name/code of the bank that will receive the export payment.",
    "Is_ACU_Payment": "Y/N flag -- whether payment is through the Asian Clearing Union mechanism.",
    "Declaration_Date": "Date of this shipping bill's declaration.",
    "Is_Provisional_Assessment": "Y/N flag -- whether this shipping bill is provisionally assessed.",
    "Is_Joint_Filing_Declaration": "Y/N flag -- whether this is a joint-filing declaration (CHA + exporter).",
    "Is_Export_under_SEZAct2005_Rule2006": "Y/N flag -- whether the export is under the SEZ Act 2005 / SEZ Rules 2006.",
    "Will_Goods_move_out_in_parts": "Y/N flag -- whether the shipment will physically move out in multiple partial consignments.",
    "Is_Free_Export_under_FTP_Para2.01": "Y/N flag -- whether claimed as a \"free export\" under Foreign Trade Policy paragraph 2.01.",
    "Is_Reward_claimed_under_MEIS": "Y/N flag -- whether a reward is claimed under MEIS (Merchandise Exports from India Scheme).",
    "Is_eCommerce_Transaction": "Y/N flag -- whether this export was sold via an e-commerce platform.",
    "eCommerce_webportal": "Name of the e-commerce platform used, if applicable.",
    "Courier_detail": "Courier company details, for courier-mode exports.",
    "FIRC_Ref_ID": "Foreign Inward Remittance Certificate reference ID.",
    "AWB_No.": "Air Waybill number (for air shipments).",
    "Is_Client_detail_applicable": "Y/N flag -- whether a separate client (end buyer) detail block applies.",
    "Client_Type": "Category of the client party.",
    "Client_IEC": "The client's Importer-Exporter Code.",
    "Client_GSTIN": "The client's GST registration number.",
    "Client_Name": "The client's name.",
    "Client_Address": "The client's address.",
    "Client_Country_Code": "The client's country code.",
    "Is_Third_Party_detail_applicable": "Y/N flag -- whether a third-party exporter detail block applies (third-party exports).",
    "Third_Party_IEC": "The third party's Importer-Exporter Code.",
    "Third_Party_BranchSrNo": "The third party's branch serial number.",
    "Invoice_Value": "Total invoice value declared for this shipment.",
    "Invoice_Value_Currency_Code": "Currency code of that invoice value.",

    # -- PACKING_DETAIL --
    "From_Packet": "Starting packet/package number in this packing range.",
    "To_Packet": "Ending packet/package number in this packing range.",
    "Packet_Unit_Code": "Unit code used for the packet count.",

    # -- STUFFING_INFO --
    "Goods_Stuffed_at": "Location where the goods were stuffed into the container (e.g. Factory, CFS).",
    "Is_Sample_Accompanied": "Y/N flag -- whether a sample accompanies the shipment for examination.",
    "CFS_Location": "Container Freight Station location used.",
    "Factory_Address": "Address of the factory where stuffing occurred (for factory stuffing).",
    "Seal_Type": "Type of seal used on the container (e.g. One-Time Lock, RFID).",
    "Seal_No": "Seal number affixed to the container.",
    "Agency_Name": "Name of the agency that supervised/sealed the stuffing.",

    # -- ANNEXURE_C1 (factory stuffing examination report) --
    "IEC_of_EOU": "Importer-Exporter Code of the EOU (Export Oriented Unit), if applicable.",
    "BranchSrNo": "Branch serial number of the exporter.",
    "Examination_Date": "Date the goods were examined.",
    "Examining_Officer": "Name of the customs officer who examined the goods.",
    "Examining_Officer_Designation": "Designation/rank of that examining officer.",
    "Supervising_Officer": "Name of the supervising officer.",
    "Supervising_Officer_Designation": "Designation/rank of that supervising officer.",
    "Commissionerate": "The customs Commissionerate having jurisdiction.",
    "Division": "The customs Division within that Commissionerate.",
    "Range": "The customs Range within that Division.",
    "Verified_by_Examining_Officer": "Y/N flag -- whether verification by the examining officer is confirmed.",
    "Is_Sample_Forwarded": "Y/N flag -- whether a sample was forwarded for testing.",
    "Seal_No": "Seal number affixed after examination (ANNEXURE_C1) or to the container (STUFFING_INFO).",

    # -- EXBOND_INFO (ex-bond clearance from a bonded warehouse) --
    # "BE_No" itself: see _MANUAL_DESCRIPTIONS_BY_SHEET -- this exact
    # sheet's own meaning ("warehoused for ex-bond export") is scoped there,
    # since "BE_No" means something different on icafe's JOBWORK sheet.
    "BE_No": "Bill of Entry number referenced by this row (see the specific sheet for what it represents).",
    "BE_Date": "Date of that Bill of Entry.",
    "Vessel": "Name of the vessel on which the goods originally arrived.",
    "Voyage": "Voyage number of that vessel.",
    "IGM_No.": "Import General Manifest number for that original arrival.",
    "IGM_Date": "Date of that Import General Manifest.",
    "No_of_Pkg": "Number of packages being cleared ex-bond.",
    "Bond_No.": "Warehousing bond number.",
    "Bond_Date": "Date of that warehousing bond.",
    "Warehouse": "Name/code of the bonded warehouse.",

    # -- CONTAINER --
    "Container_No.": "Container number.",
    "Container_Type": "Type/size of container (e.g. 20ft, 40ft, Reefer).",
    "Packages_Stuffed": "Number of packages stuffed into this container.",
    "Gross_Weight_Unit_Code": "Unit code for the container's gross weight.",
    "Seal_No.": "Seal number on this container.",
    "Seal_Date": "Date the seal was affixed.",
    "Seal_Device_ID": "Electronic seal device identifier, if an e-seal is used.",
    "Movement_Document_Type": "Type of document authorizing this container's movement (e.g. gate pass, transport permit).",
    "Movement_Document_No.": "Number of that movement document.",

    # -- INVOICES --
    "InvSrNo": "Serial number of this invoice within the shipping bill (a shipping bill can cover multiple invoices).",
    "Inv_No": "The commercial invoice number.",
    "TOI_Place": "The named place associated with this invoice's Incoterm (e.g. \"FOB Mumbai\").",
    "Inv_Currency_Code": "Currency code this invoice is denominated in.",
    "Price_Includes_N_F_I_B": (
        "UNCONFIRMED MEANING -- appears to flag whether the price includes Normal/Freight/"
        "Insurance/[B] components; verify the exact breakdown against a real filed shipping bill."
    ),
    "Taxable_value_for_IGST": "The value used as the basis for IGST calculation.",
    "Product_Value": "Value of the goods alone, excluding freight/insurance/other charges.",
    "Packing_FOB_Amount": "FOB value of the packing charges.",
    "Freight_Currency_Code": "Currency of the freight charge.",
    "Freight_Percentage": "Freight expressed as a percentage of value, if applicable.",
    "Freight_Amount": "Freight charge amount.",
    "Insurance_Currency_Code": "Currency of the insurance charge.",
    "Insurance_Percentage": "Insurance expressed as a percentage of value, if applicable.",
    "Insurance_Amount": "Insurance charge amount.",
    "Discount_Currency": "Currency of any discount applied.",
    "Discount_Percentage": "Discount as a percentage.",
    "Discount_Amount": "Discount amount.",
    "Other_Deduction_Currency": "Currency of any other deduction.",
    "Other_Deduction_Percentage": "Other deduction as a percentage.",
    "Other_Deduction_Amount": "Other deduction amount.",
    "Commission_Currency": "Currency of any commission.",
    "Commission_Percentage": "Commission as a percentage.",
    "Commission_Amount": "Commission amount.",
    "Third_Party_Name": "Name of the third party associated with this invoice/shipment.",
    "Third_Party_Address": "The third party's address.",
    "Third_Party_City": "The third party's city.",
    "Third_Party_PIN": "The third party's postal/PIN code.",
    "Third_Party_State": "The third party's state.",
    "Third_Party_Country_Code": "The third party's country code.",
    "Buyer_Name": "The buyer's name for this invoice.",
    "Buyer_Address": "The buyer's address.",
    "Buyer_City": "The buyer's city.",
    "Buyer_PIN": "The buyer's postal/PIN code.",
    "Buyer_State": "The buyer's state.",
    "Buyer_Country_Code": "The buyer's country code.",
    "Export_Contract_No.": "The export sales contract number.",
    "Export_Contract_Date": "Date of that export contract.",
    "Nature_of_Payment": "Category of payment arrangement (e.g. Advance, Open Account, Letter of Credit).",
    "Payment_Period": "Credit period allowed for payment (e.g. number of days).",
    "AEO_Code": "Authorized Economic Operator code, if the exporter/invoice qualifies for AEO benefits.",
    "AEO_Country_Code": "Country code of the AEO scheme being claimed under.",
    "AEO_Role": "The AEO role/category applicable.",

    # -- ITEMS (the shipping bill's own item table) --
    "ItemSrNo": "Serial number of this item within its invoice.",
    "RITC": "The RITC (Revised Indian Trade Classification) / HSN code for this item.",
    "Quantity_Unit_Code": "Unit code for this item's Quantity.",
    "Unit_Price": "Price per unit.",
    "Per_Unit_No.": "The \"per\" basis the unit price is quoted against (e.g. price per 100 units).",
    "SQC_Quantity": "Statistical Quantity -- the quantity expressed in the standard unit used for trade statistics.",
    "SQC_Quantity_Unit_Code": "Unit code for that statistical quantity.",
    "Exim_Scheme_Code": "The EXIM (export-import policy) scheme code this item is exported under.",
    "NFEI_Category_Code": "Net Foreign Exchange Earnings category code (relevant to EOU/SEZ incentive calculations).",
    "PMV": "Present Market Value of the item.",
    "PMV_Currency_Code": "Currency of that present market value.",
    "IGST_Pay_Status": "Whether IGST has been paid, is exempted, or is under LUT/bond for this item.",
    "IGST_Taxable_Amt": "Taxable amount for IGST purposes.",
    "IGST_Rate": "IGST rate applicable.",
    "IGST_Amount": "IGST amount.",
    "CompCessRate": "GST Compensation Cess rate applicable.",
    "Is_Reward_Item": "Y/N flag -- whether this item is claimed under a reward/incentive scheme (e.g. RoDTEP, MEIS).",
    "End_Use_Code": "Declared end-use code for the goods.",
    "Origin_District_Code": "District code (within India) where the item originated.",
    "PTAFTA_Code": "Preferential Trade Agreement / Free Trade Agreement code claimed for this item.",
    "Accessories_Status": "Whether accessories are included with this item.",
    "Accessory_Info": "Details of those accessories.",
    "DBK_Sr._No": "Drawback schedule serial number claimed for this item.",
    "DBK_Quantity": "Quantity against which drawback is claimed.",
    "DBK_Under": "The drawback scheme/section claimed under.",
    "Product_Material_Code": "Internal/customs material code for the product.",
    "TP_Exporter_Name": "Third-party exporter's name for this item (third-party export transactions).",
    "TP_Exporter_IE_Code": "Third-party exporter's Importer-Exporter Code.",
    "TP_Exporter_BrS_No.": "Third-party exporter's branch serial number.",
    "TP_Exporter_Reg_Type": "Type of registration held by the third-party exporter.",
    "TP_Exporter_Reg_No": "Registration number of the third-party exporter.",
    "TP_Exporter_Addr": "Third-party exporter's address.",
    "Manufacturer": "Name of the actual manufacturer of the goods.",
    "Manufacturer_Addr": "The manufacturer's address.",
    "Manufacturer_CodeType": "The type/scheme of the manufacturer code given below.",
    "Manufacturer_Code": "The manufacturer's registration code.",
    "Manufacturer_Country": "The manufacturer's country.",
    "Manufacturer_State": "The manufacturer's state/province.",
    "Manuf_Postal_Code": "The manufacturer's postal/ZIP code.",
    "SourceState": "The Indian state the goods were sourced from.",
    "TransitCountry": "Country through which the goods transited.",
    "CENVAT_Certificate_No.": "Number of a CENVAT (legacy excise input credit) certificate associated with this item.",
    "CENVAT_Certificate_Date": "Date that CENVAT certificate was issued.",
    "CENVAT_Certificate_Valid_upto": "Validity expiry date of that CENVAT certificate.",
    "CENVAT_Central_Excise_Office_Code": "Central Excise office code that issued that CENVAT certificate.",
    "CENVAT_Assesse_Code": "The assessee code registered on that CENVAT certificate.",
    "RoDTEP_Status": "Whether RoDTEP (Remission of Duties and Taxes on Exported Products) benefit is claimed for this item.",
    "RoDTEP_Quantity": "Quantity against which RoDTEP is claimed.",
    "RoDTEP_Quantity_Unit_Code": "Unit code for that RoDTEP quantity.",
    "Medicinal_Plant": "Whether the item is a medicinal plant product (subject to special export regulation).",
    "Formulation": "Details of the formulation, for pharmaceutical/chemical items.",
    "Surface_Material_in_Contact": "The material in contact with the product's surface (relevant for food/pharma packaging declarations).",
    "GST_Allowed": "Y/N flag -- whether GST input credit is allowed for this item.",
    "LGD_Code": (
        "UNCONFIRMED MEANING -- possibly a \"Legal Goods Description\" or scheme-specific code; "
        "verify against a real filed shipping bill before trusting this."
    ),

    # -- DEEC / EPCG / DFIA (identical field set -- a license-utilization block
    # repeated per export-incentive scheme: DEEC = Advance Authorization,
    # EPCG = Export Promotion Capital Goods, DFIA = Duty Free Import
    # Authorization. Each ties an exported item back to the duty-free import
    # entitlement it earns under that specific license.) --
    "License_Reference_No.": "The license reference number for this scheme (Advance Authorization/EPCG/DFIA, per the sheet).",
    "License_Registration_No": "That license's registration number at customs.",
    "License_Registration_Date": "Date of that registration.",
    "Export_Item_SrNo.": "Serial number of this export item as listed on the license.",
    "Export_Item_Qty": "Quantity of this export item being debited against the license.",
    "Import_Item_SrNo.": "Serial number of the corresponding duty-free import item allowed under this license.",
    "Import_Item_Type": "Category/type of that duty-free import item.",
    "Import_Item_Description": "Description of that duty-free import item.",
    "Import_Item_Quantity": "Quantity of that duty-free import item.",
    "Import_Item_Quantity_Unit_Code": "Unit code for that import item quantity.",

    # -- DFRC (Duty Free Replenishment Certificate -- legacy predecessor to DFIA) --
    "Regn_No": "The DFRC registration number.",
    "Prod_Group": "Product group classification under the DFRC/SION framework.",
    "SION_Sr_No": "Standard Input-Output Norms (SION) serial number applicable.",
    "SION_IONorm_SNo": "The specific input-output norm serial number within that SION entry.",
    "Qty": "Quantity.",
    "Unit": "Unit of that quantity.",
    "Item_Type": "Category of the item.",
    "Item_Description": "Description of the item.",
    "Technical_Details": "Technical specification details of the item.",

    # -- CESS_AND_DUTY --
    "Export_Duty_Schedule": "The export duty schedule/notification applicable to this item.",
    "Quantity_for_Export_Duty": "Quantity on which export duty is computed.",
    "Cess_Schedule": "The cess schedule/notification applicable.",
    "Tariff_Value_for_Cess": "The tariff value used as the basis for cess calculation.",
    "Tariiff_Value_Unit_Code": "Unit code for that tariff value.",
    "Quantity_for_Cess": "Quantity on which cess is computed.",
    "Other_Duty/Cess_Schedule": "A further, additional duty/cess schedule applicable to this item.",
    "Quantity_for_Other_Duty/Cess": "Quantity for that additional duty/cess.",
    "Third_Cess_Schedule": "A third cess schedule, if applicable.",
    "Quantity_for_Third_Cess": "Quantity for that third cess.",

    # -- QUOTA --
    "Certificate_No.": "Export quota certificate number.",
    "Agency_Code": "Code of the agency that issued the quota certificate.",
    "Expiry_Date": "Expiry date of that quota certificate.",

    # -- ARE_DETAILS (ARE-1: Central Excise removal for export) --
    "ARE_No.": "The ARE-1 (Application for Removal of Excisable Goods for Export) number.",
    "ARE_Date": "Date of that ARE-1.",
    "Remarks": "Any remarks noted on the ARE-1.",

    # -- JOB_WORK --
    "Notification_No.": "The job-work notification number under which this export is made.",
    "BE_Inv_SrNo.": "The referenced Bill of Entry's own invoice serial number.",
    "BE_Actual_Inv_No.": "The referenced Bill of Entry's own actual invoice number.",
    "BE_Item_SrNo": "The referenced Bill of Entry's own item serial number.",
    "Imported_at": "The port/location where the original goods were imported.",
    "BE_Quantity": "Quantity from that Bill of Entry being exported after job work.",
    "BE_Quantity_Unit_Code": "Unit code for that Bill of Entry quantity.",

    # -- RE-EXPORT --
    "BE_Inv_SNo": "The referenced import Bill of Entry's own invoice serial number.",
    "BE_Item_SNo": "The referenced import Bill of Entry's own item serial number.",
    "BE_Import_Port_Code": "Port code where the original import happened.",
    "Manual_BE": "Y/N flag -- whether that Bill of Entry was manually filed (not electronic).",
    "BE_Item_Description": "Description of the item as it appeared on the import Bill of Entry.",
    "Imported_Quantity": "Quantity originally imported.",
    "Imported_Quantity_Unit_Code": "Unit code for that imported quantity.",
    "Assessable_Value": "Assessable value at the time of import.",
    "Tot_Duty_Paid": "Total duty paid at the time of import.",
    "Duty_Payment_Date": "Date that duty was paid.",
    "Exported_Quantity": "Quantity now being re-exported.",
    "Exported_Quantity_Unit_Code": "Unit code for that re-exported quantity.",
    "Is_Input_Credit_Availed": "Y/N flag -- whether input tax credit was availed on this item.",
    "Is_Personal_Use_Item": "Y/N flag -- whether the item was put to personal/own use before re-export.",
    "Other_Identifying_Parameters": "Any other identifying details distinguishing this specific unit/item.",
    "Obligation_No.": "Reference number of any export obligation tied to this re-export.",
    "DBK_Amount_Claimed": "Drawback amount being claimed on re-export.",
    "Is_Item_Un-Used": "Y/N flag -- whether the item remains unused since import.",
    "Is_Commissioner_Permission": "Y/N flag -- whether Commissioner's permission was obtained for this re-export.",
    "Board_No.": "Reference number of a CBIC (Board) circular/permission relied upon.",
    "Board_Date": "Date of that Board circular/permission.",
    "Is_MODVAT_Availed": "Y/N flag -- whether MODVAT (legacy excise credit) was availed.",
    "Is_MODVAT_Reversed": "Y/N flag -- whether that MODVAT credit was reversed for this re-export.",

    # -- DUC --
    "DUC Reference No.": "Duty Credit Scrip / DUC reference number.",
    "Export Type": "Category of export this DUC reference applies to.",
    "SB Date": "The Shipping Bill's own date.",
    "BE No.": "Bill of Entry number referenced by this DUC entry.",
    "BE Date": "Date of that referenced Bill of Entry.",
    "BE Filed At": "Port/location where that Bill of Entry was filed.",
    "BE Inv Sr": "That Bill of Entry's own invoice serial number.",
    "BE Item Sr": "That Bill of Entry's own item serial number.",

    # -- SW_ADDL_INFO (Single Window additional info -- Participating
    # Government Agency declarations beyond customs) --
    "Info_Type": "The Single Window information type code, as defined by the regulating PGA (Participating Government Agency).",
    "info_Qualifier": "A qualifier further specifying that information type.",
    "Info_Code_Description": "Description of the information code.",
    "Information": "The actual information/value being declared.",
    "Measure": "A numeric measure associated with this declaration.",
    "Measure_Unit": "Unit of that measure.",

    # -- SW_CONSTITUENT (Single Window constituent/ingredient declaration) --
    "Cons_Name": "Name of the constituent/ingredient.",
    "Cons_Code": "Code identifying that constituent.",
    "Cons_Percentage": "Percentage of this constituent in the product.",
    "Cons_Yield%": "Yield percentage of this constituent.",
    "Cons_Active": "Y/N flag -- whether this is an active ingredient/constituent.",

    # -- SW_PRODUCTION (Single Window production/batch declaration) --
    "Prod_Batch_ID": "Production batch identifier.",
    "Prod_Batch_Quantity": "Quantity produced in that batch.",
    "Prod_Batch_Unit": "Unit of that batch quantity.",
    "Prod_Manufacturer_Date": "Date of manufacture.",
    "Prod_Expiry_Date": "Expiry date.",
    "Prod_Best_before_Date": "Best-before date.",

    # -- SW_CONTROL (Single Window regulatory control/inspection declaration) --
    "Control_Type": "Type of regulatory control/inspection applied.",
    "Control_Location": "Location where that control was carried out.",
    "Control_Start_Date": "Start date of that control.",
    "Control_End_Date": "End date of that control.",
    "Control_Result": "Result/outcome of that control.",

    # -- STATEMENT --
    "StatementType": "Category of statement being declared.",
    "StatementCode": "Code identifying that statement.",
    "StatementRemarks": "Free-text remarks for that statement.",

    # -- E-SANCHIT (India Customs' electronic document-upload system --
    # supporting documents attached to this filing) --
    "Doc_ICEGATE_ID": "The ICEGATE-assigned ID for this uploaded document.",
    "Doc_IRN": "The document's Image Reference Number (e-Sanchit's unique document identifier).",
    "Doc_Upload_DateTime": "Date/time the document was uploaded.",
    # "Doc_Type" on THIS sheet specifically: see _MANUAL_DESCRIPTIONS_BY_SHEET
    # -- reused on icafe's Container sheet with a different meaning.
    "Doc_Type": "The type/category of this document.",
    "File_Type": "File format of the uploaded document.",
    "Icegate_File_Name": "The filename as registered on ICEGATE.",
    "Document_Name": "Descriptive name of the document.",
    "Doc_Issued_At": "Place the document was issued.",
    "Doc_Issued_Date": "Date the document was issued.",
    "Doc_Expiry_Date": "Expiry date of the document, if applicable.",
    "Doc_Issuing_Party_Name": "Name of the party that issued this document.",
    "Doc_Issuing_Party_Code": "Code of the party that issued this document.",
    "Doc_Issuing_Party_Add1": "Issuing party's address line 1.",
    "Doc_Issuing_Party_Add2": "Issuing party's address line 2.",
    "Doc_Issuing_Party_City": "Issuing party's city.",
    "Doc_Issuing_Party_Pin_Code": "Issuing party's postal/PIN code.",
    "Doc_Beneficiary_Party_Name": "Name of the party this document benefits/is addressed to.",
    "Doc_Beneficiary_Party_Code": "Code of that beneficiary party.",
    "Doc_Beneficiary_Party_Add1": "Beneficiary party's address line 1.",
    "Doc_Beneficiary_Party_Add2": "Beneficiary party's address line 2.",
    "Doc_Beneficiary_Party_City": "Beneficiary party's city.",
    "Doc_Beneficiary_Party_Pin_Code": "Beneficiary party's postal/PIN code.",

    # -- EXCHANGE_RATE --
    "CURRENCY_CODE": "The foreign currency this exchange rate applies to.",
    "EXCHANGE_RATE": "The exchange rate value (foreign currency to INR).",
    "BANK_NAME": "Name of the bank whose rate/certificate is being used.",
    "BANK_CERTIFICATE": "Reference to the bank's exchange rate certificate.",
    "BANK_CERTIFICATE_DATE": "Date of that bank certificate.",

    # -- Several sheets above (QUOTA, ARE_DETAILS, JOB_WORK, SW_ADDL_INFO,
    # SW_CONSTITUENT, SW_PRODUCTION, SW_CONTROL, STATEMENT, E-SANCHIT) use
    # "Inv_SrNo"/"Item_SrNo" WITH an underscore -- a genuinely different
    # literal column name from ITEMS/DEEC/EPCG/DFIA/DFRC/CESS_AND_DUTY/
    # RE-EXPORT/DUC's own "InvSrNo"/"ItemSrNo" (no underscore), even though
    # both mean the same thing (the invoice/item this row belongs to).
    "Inv_SrNo": "Serial number of the invoice this row's data belongs to (links back to the INVOICES sheet).",
    "Inv_SrNo.": "Serial number of the invoice this row's data belongs to (links back to the INVOICES sheet).",
    "Item_SrNo": "Serial number of the item this row's data belongs to (links back to the ITEMS sheet).",
    "Product_Desc": "Commercial description of the goods for this line item.",
    "BE_No.": "Bill of Entry number referenced by this row.",
    "Reference_No.": "The document's own reference number.",
})

# DRAFT, not yet domain-confirmed (same caveat as the blocks above): the
# "iCaffeExcel_Export.xls" ("icafe") block below is a first pass for
# India's classic ICEGATE Shipping Bill format (SB_1 = exporter/consignee
# header, SB_2 = packing/container/shipment logistics) -- the same domain
# as export_xlsx above, just an older/differently-shaped EDI layout, so
# many fields here are conceptually identical to that block's (AR4,
# THIRDPARTY, JOBWORK, License, Container, ESanchit). Fields flagged
# UNCONFIRMED inline are genuinely uncertain and should be verified against
# a real filed Shipping Bill.
_MANUAL_DESCRIPTIONS.update({
    # -- SB_1 (exporter/consignee header) --
    "Location_Code": "Customs location/port code where this Shipping Bill is filed.",
    "Govt.Pvt_IEC": "Whether the IEC holder is a Government or Private entity.",
    "IEC_Code": "Importer-Exporter Code of the exporter.",
    "Branch Sr_No": "The exporter's branch serial number.",
    "Exporter_Address_1": "Exporter's address line 1.",
    "Exporter_Address_2": "Exporter's address line 2.",
    "Exporter_City": "Exporter's city.",
    "Exporter_State": "Exporter's state.",
    "Pin_Code": "Exporter's postal/PIN code.",
    "NFEI": "Net Foreign Exchange Earnings category/code.",
    "GSTN_Type_Code": "Type of GST registration held.",
    "GSTIN_No": "The exporter's GST registration number.",
    "Merchant_Manufacturer": "Whether the exporter is a Merchant or Manufacturer exporter.",
    "Class": "Exporter classification/category.",
    "Sub_Agent details": "Whether sub-agent (CHA sub-agent) details apply to this filing.",
    "SubAgent_Name": "Name of the sub-agent handling this filing.",
    "Sub_Agent_Address": "The sub-agent's address.",
    "Consignee_Name": "Name of the consignee.",
    "Consignee_Address_1": "Consignee's address line 1.",
    "Consignee_Address_2": "Consignee's address line 2.",
    "Consignee_Address_3": "Consignee's address line 3.",
    "Consignee_Address_4": "Consignee's address line 4.",
    "Country_Code": "The consignee's country code.",
    "Loading_Port_Code": "Port code where the goods are loaded.",
    "CountryOfDestination_Code": "Final destination country code.",
    "Destination_Port_Code": "Final destination port code.",
    "CountryOfDischarge_Code": "Country code of the discharge port.",
    "Discharge_Port": "Port of discharge.",
    "Drawback_A/C_No": "Bank account number into which the drawback amount will be credited.",
    "Forex_Code": "Foreign exchange code.",
    "PAN_No": "Exporter's Permanent Account Number (income tax ID).",
    "User_Job_No": "The filing agent's own internal job reference number.",
    "CATEGORY": "Shipping bill category.",
    "RBI_WVR_NO": "RBI GR (Guaranteed Remittance) waiver number.",
    "RBI_WVR_DT": "Date of that RBI GR waiver.",
    "EPZ_ICD": "Export Processing Zone / Inland Container Depot code, if applicable.",

    # -- INVOICE --
    "Invoice_Sr_No": "Serial number of this invoice within the shipping bill.",
    "Invoice_No": "The commercial invoice number.",
    "Invoice_Date": "The invoice's own date.",
    "Non_Stnd_Currency": "A non-standard currency name/code, when the invoice uses one not in the standard currency list.",
    "Nature_Payment": "Category of payment arrangement.",
    "Pymt_Period": "Credit period allowed for payment.",
    "Consignee_and_Buyer_same": "Y/N flag -- whether the buyer is the same party as the consignee.",
    "Address_1": "Buyer's address line 1.",
    "Address_2": "Buyer's address line 2.",
    "Address_3": "Buyer's address line 3.",
    "Address_4": "Buyer's address line 4.",
    "Nature_of_Contract(CF/CI/CIF/FOB)": "The Incoterm basis of this sales contract (e.g. CF, CI, CIF, FOB).",
    "Add(Freight/Insurance/None/Both)(F/I/N/B)": "Flag for whether Freight, Insurance, Neither, or Both are additionally included in the price.",
    "Term_Place": "The named place for the delivery term (e.g. \"FOB Chennai\").",
    "Freight_Rate": "Freight charge rate.",
    "Freight_Currency_Code": "Currency of the freight charge.",
    "Freight_Amount": "Freight charge amount.",
    "Insurance_Rate": "Insurance charge rate.",
    "Insurance_Currency_Code": "Currency of the insurance charge.",
    "Insurance_Amount": "Insurance charge amount.",
    "Packing_Misc_Charge_Rate": "Packing/miscellaneous charge rate.",
    "Packing_Currency_Code": "Currency of the packing/miscellaneous charge.",
    "Packing_Misc_Charge_Amount": "Packing/miscellaneous charge amount.",
    "Discount_On_Fob_Rate": "Discount on FOB value, as a rate.",
    "Discount_Currency_Code": "Currency of the discount.",
    "Discount_On_Fob_Amount": "Discount on FOB value, as an amount.",
    "Other_Deductions_Rate": "Other deduction, as a rate.",
    "Other_Deductions_Currency_Code": "Currency of the other deduction.",
    "Other_Deductions_Amount": "Other deduction amount.",
    "Commission_Rate": "Commission rate.",
    "Commission_Currency_Code": "Currency of the commission.",
    "Commission_Amount": "Commission amount.",
    "Third_Party_Details": "Y/N flag -- whether third-party details apply to this invoice.",
    "ThirdParty_Address_1": "Third party's address line 1.",
    "ThirdParty_Address_2": "Third party's address line 2.",
    "ThirdParty_City": "Third party's city.",
    "ThirdPartyCountry_div_Code": "Third party's country subdivision (state/province) code.",
    "ThirdPartyPin_Code": "Third party's postal/PIN code.",

    # -- Item --
    "Inv Serial No": "Serial number of the invoice this item belongs to.",
    "Goods Description1": "Commercial description of the goods for this item.",
    "Scheme Code": "The export scheme code this item is shipped under.",
    "Ritc": "RITC (Revised Indian Trade Classification) / HSN code for this item.",
    "Unit of Qty": "Unit of measure for this item's Quantity.",
    "Rate Value": "Unit price/rate for this item.",
    "Unit of Rate": "The \"per\" unit basis the rate is quoted against.",
    "No of Unit (Per)": "The quantity basis for that rate (e.g. price per 100 units).",
    "PMVPercent": "A percentage applied relative to PMV (e.g. for a drawback cap calculation).",
    "Accessories": "Whether accessories are included with this item.",
    "Quota": "Export quota reference/details, if quota applies to this item.",
    "Cess": "Cess applicability flag/details for this item.",
    "Third Party": "Whether a third party applies to this item.",
    "AR4": "Whether an ARE-1 (AR4) applies to this item.",
    "REWARD": "Whether a reward scheme (e.g. RoDTEP, MEIS) is claimed for this item.",
    "STR": "UNCONFIRMED MEANING -- a scheme/status flag; verify against a real filed shipping bill.",
    "IGST_PAYMENT_STATUS": "Whether IGST has been paid, is exempted, or is under LUT/bond for this item.",
    "TAXABLE_VALUE": "Taxable value for IGST purposes.",
    "IGST_AMOUNT": "IGST amount.",
    "SOURCE_STATE": "The Indian state the goods were sourced from.",
    "TRANSIT_CTRY": "Country through which the goods transited.",
    "ACCESSORY_STATUS": "Status of the accessory declaration for this item.",
    "HAWB_NO": "House Airway Bill number.",
    "TOTAL_PKG": "Total packages for this item.",
    "MNF_NAME": "Manufacturer's name.",
    "MNF_CODE_TYPE": "Type/scheme of the manufacturer code.",
    "MNF_CODE": "Manufacturer's registration code.",
    "MNF_ADD1": "Manufacturer's address line 1.",
    "MNF_ADD2": "Manufacturer's address line 2.",
    "MNF_CITY": "Manufacturer's city.",
    "MNF_CTRY_SUBDV": "Manufacturer's country subdivision (state/province).",
    "MNF_PIN": "Manufacturer's postal/PIN code.",
    "MNF_CTRY": "Manufacturer's country.",
    "SWI_STO": "UNCONFIRMED MEANING -- a Single Window Interface field; verify against a real filed shipping bill.",
    "SWI_DOO": "UNCONFIRMED MEANING -- a Single Window Interface field; verify against a real filed shipping bill.",
    "SWI_EPT": "UNCONFIRMED MEANING -- a Single Window Interface field; verify against a real filed shipping bill.",
    "SWI_UQC": "Single Window Interface unit of measure (UQC) for the quantity below.",
    "SWI_QTY": "Single Window Interface declared quantity.",
    "SWI_GCESS_AMT": "Single Window Interface GST Compensation Cess amount.",
    "SWI_GCESS_CUR": "Currency of that Single Window Interface GST Compensation Cess amount.",
    "DBK_SRNO": "Drawback schedule serial number claimed for this item.",
    "DBK_QUANTITY": "Quantity against which drawback is claimed.",
    "RODTEP_FLG": "Y/N flag -- whether RoDTEP benefit is claimed for this item.",
    "IDENTIFIERS_INFO_CODE": "UNCONFIRMED MEANING -- an identifier/info code field; verify against a real filed shipping bill.",
    "IGST_RATE": "IGST rate applicable.",

    # -- License (Advance Authorization/EPCG-style license utilization) --
    "INV_SRNO": "Serial number of the invoice this row's data belongs to.",
    "ITM_NO": "Serial number of the item this row's data belongs to.",
    "EXP_QTY": "Export quantity being debited against the license.",
    "Sr_No": "Serial number of this entry.",
    "REGN_NO": "License registration number.",
    "REGN_DT": "Date of that license registration.",
    "SLNO_E": "The export item's serial number as listed on the license.",
    "SLNO_C": "The corresponding duty-free import item's serial number as listed on the license.",
    "QTY": "Quantity of the duty-free import item allowed under this license.",
    "UQC": "Unit of measure (UQC) for that quantity.",
    "ITEM_DESC": "Description of the duty-free import item.",
    "TECH_CHAR": "Technical characteristics of the item.",
    "IND_IMP": "Indicator for the import item's type/category.",
    "LIC_NO": "License number.",
    "LIC_DATE": "License date.",
    "LIC_TYPE": "License type (e.g. Advance Authorization, EPCG, DFIA).",

    # -- THIRDPARTY --
    "IEC_No": "The third party's Importer-Exporter Code.",
    "Branch_Sr_No": "The third party's branch serial number.",
    "City": "The third party's city.",
    "Pin": "The third party's postal/PIN code.",
    "GSTN_Type": "Type of GST registration held by the third party.",
    "GSTN_Id": "The third party's GST registration number.",

    # -- JOBWORK --
    # "Notification_No" and "BE_No" on THIS sheet specifically: see
    # _MANUAL_DESCRIPTIONS_BY_SHEET -- both names are reused elsewhere with
    # a different meaning, so their JOBWORK-specific text lives there now.
    "Be_Date": "Date of that Bill of Entry.",
    "BE_Inv_No": "That Bill of Entry's own invoice number.",
    "Actual_Inv_No": "The actual invoice number recorded on that Bill of Entry.",
    "Be_Item_No": "That Bill of Entry's own item number.",
    "Port": "The port where the original import happened.",
    "FREIGHT": "Freight amount for this job-work shipment.",
    "INSURANCE": "Insurance amount for this job-work shipment.",

    # -- AR4 (ARE-1: Central Excise removal for export) --
    "Inv_Sr_No": "Serial number of the invoice this row's data belongs to.",
    "Ar4_No": "The ARE-1 (AR4) number.",
    "Ar4_Date": "Date of that ARE-1.",

    # -- CESS --
    "Cess_No": "Cess notification number applicable.",
    "Cess_Sr_No": "Serial number within that cess notification.",
    "Cess_Adv": "UNCONFIRMED MEANING -- likely a cess advance-licence reference or advance cess amount; verify against a real filed shipping bill.",

    # -- EOU_Details (factory stuffing examination, EOU shipments) --
    "Inv_Srno": "Serial number of the invoice this row's data belongs to.",
    "Itm_Srno": "Serial number of the item this row's data belongs to.",
    "Examin_Date": "Date the goods were examined.",
    "Examiner_name": "Name of the examining officer.",
    "Examiner_designation": "Designation/rank of the examining officer.",
    "Supervisior_Name": "Name of the supervising officer.",
    "Supervisior_Designation": "Designation/rank of the supervising officer.",
    "Commissione_Rate": "The customs Commissionerate having jurisdiction.",
    "Item_Value_Verified": "Y/N flag -- whether the item's value has been verified.",
    "Sample": "Y/N flag -- whether a sample was drawn/forwarded.",
    "IE_CODE_EOU": "Importer-Exporter Code of the EOU (Export Oriented Unit).",

    # -- SB_2 (packing/container/shipment logistics) --
    "Sample_Accompanied": "Y/N flag -- whether a sample accompanies the shipment.",
    "Factory_Stuffed": "Y/N flag -- whether the container was stuffed at the factory.",
    "Nature_Of_Cargo": "Category of cargo (e.g. Containerized, Breakbulk, Bulk, LCL).",
    "Package_Unit": "Unit code for the package count.",
    "Marks_And _No": "Shipping marks and numbers printed on the packages.",
    "Total_Loose_Pkgs": "Total loose (unpacked) packages in the shipment.",
    "Weight_Unit": "Unit of weight used (e.g. KGS, TON).",
    "No_Of_Containers": "Number of containers in the shipment.",
    "Total_Pkgs_In_MBL": "Total packages as declared on the Master Bill of Lading.",
    "TransShipper_Code": "Transshipment agent/carrier code.",
    "Gateway_Port": "Gateway port used for transshipment.",
    "Sb_Number": "The Shipping Bill's own number.",
    "SB_Date": "The Shipping Bill's own date.",
    "EP_Copy_No": "Export Promotion copy number (used to claim export incentives).",
    "EP_Copy_Date": "Date of that Export Promotion copy.",
    "GRN_No": "Goods Receipt Note number.",
    "PO_No": "Purchase order number.",
    "Service_Type": "Category of shipping service.",
    "Head_Desc": "Header description.",
    "Packing Details": "Y/N flag -- whether packing detail rows apply to this shipment.",
    "Container_Details": "Y/N flag -- whether container detail rows apply to this shipment.",
    "AirlineName": "Airline name (for air shipments).",
    "MawbNumber": "Master Airway Bill number.",
    "Mawb_Date": "Master Airway Bill date.",
    "HawbNumber": "House Airway Bill number.",
    "Hawb_Date": "House Airway Bill date.",

    # -- Packing --
    "Packing_No_From": "Starting packet/package number in this packing range.",
    "Packing_No_To": "Ending packet/package number in this packing range.",
    "Type_Of_Pkts": "Type of packages.",

    # -- Container --
    "Container_No": "Container number.",
    "Size": "Container size (e.g. 20ft, 40ft).",
    "No_Of_Pkts_Stuffed": "Number of packages stuffed into this container.",
    # "Doc_Type" on THIS sheet specifically: see _MANUAL_DESCRIPTIONS_BY_SHEET.
    "Doc_no": "Number of that movement document.",
    "Equipment_Type": "Type of equipment (container) used.",
    "Equipment_Qty": "Quantity of that equipment.",
    "Equipment_Qty_Code": "Unit code for that equipment quantity.",

    # -- ESanchit (electronic document upload, same concept as export_xlsx's E-SANCHIT) --
    "INV_SR_NO": "Serial number of the invoice this row's data belongs to.",
    "ITEM_SR_NO": "Serial number of the item this row's data belongs to.",
    "FILE_TYPE": "File format of the uploaded document.",
    "ICEGATEID": "The ICEGATE-assigned ID for this uploaded document.",
    "IRN": "The document's Image Reference Number (e-Sanchit's unique document identifier).",
    "DOC_TYPE_CODE": "Type/category code of the uploaded document.",
    "DRN": "UNCONFIRMED MEANING -- possibly a Document Reference Number distinct from IRN; verify against a real e-Sanchit upload.",
    "DOC_ISSUED_PLACE": "Place the document was issued.",
    "DOC_ISSUED_DATE": "Date the document was issued.",
    "DOC_EXPIRY_DATE": "Expiry date of the document, if applicable.",
    "DOC_ISSUING_PARTY_NAME": "Name of the party that issued this document.",
    "DOC_ISSUING_PARTY_CODE": "Code of the party that issued this document.",
    "DOC_ISSUING_PARTY_ADD1": "Issuing party's address line 1.",
    "DOC_ISSUING_PARTY_ADD2": "Issuing party's address line 2.",
    "DOC_ISSUING_PARTY_CITY": "Issuing party's city.",
    "DOC_ISSUING_PARTY_PIN_CODE": "Issuing party's postal/PIN code.",
    "DOC_BENEFICIARY_PARTY_NAME": "Name of the party this document benefits/is addressed to.",
    "DOC_BENEFICIARY_PARTY_CODE": "Code of that beneficiary party.",
    "DOC_BENEFICIARY_PARTY_ADD1": "Beneficiary party's address line 1.",
    "DOC_BENEFICIARY_PARTY_ADD2": "Beneficiary party's address line 2.",
    "DOC_BENEFICIARY_PARTY_CITY": "Beneficiary party's city.",
    "DOC_BENEFICIARY_PARTY_PIN_CODE": "Beneficiary party's postal/PIN code.",
    "Currency_Code": "The invoice currency code.",
    "ThirdParty_Country_Code": "Third party's country code.",
    "END_USE": "Declared end-use code for the goods.",
    "Item_No": "Serial number of the item this row's data belongs to (links back to the Item sheet).",
})

# DRAFT, not yet domain-confirmed (same caveat as the blocks above): the
# "ImportExcel (3).xls" ("import_excel_3") block below is a first pass for
# an India import Bill of Entry EDI format -- the import counterpart to
# export_xlsx above (IGST/BCD/SWS/CVD notifications, SVB -- Special
# Valuation Branch -- for related-party pricing, anti-dumping duty,
# Single Window PGA declarations, e-Sanchit documents). Fields flagged
# UNCONFIRMED inline are genuinely uncertain and should be verified against
# a real filed Bill of Entry.
_MANUAL_DESCRIPTIONS.update({
    # -- CONTAINERS --
    "NO": "Serial number of this container entry.",
    "IGM_NO": "Import General Manifest number.",
    "IGM_DT": "Import General Manifest date.",
    "LCFC": "UNCONFIRMED MEANING -- a container/location-related code; verify against a real filed Bill of Entry.",
    "CONT_NO": "Container number.",
    "CONT_SIZE": "Container size (e.g. 20ft, 40ft).",
    "TRUCK_NO": "Truck/vehicle number used for container movement.",
    "LOCATION_ID": "Location/yard ID where the container is held.",

    # -- INVOICE --
    "InvoiceNo": "The commercial invoice number.",
    "InvoiceDate": "The invoice's own date.",
    "Currency": "The invoice currency.",
    "TotalValue": "Total invoice value.",
    "Terms": "The Incoterm/delivery terms for this invoice.",
    "FrtAmt": "Freight amount.",
    "FrtCur": "Currency of the freight amount.",
    "FrtRate": "Freight rate.",
    "InsAmt": "Insurance amount.",
    "InsCur": "Currency of the insurance amount.",
    "InsRate": "Insurance rate.",
    "SupplierName": "Name of the supplier (the foreign seller).",
    "SupplierCountry": "The supplier's country.",
    "SupplierPinCode": "The supplier's postal/PIN code.",
    "SellerName": "The seller's name, if different from the supplier (e.g. a trading intermediary).",
    "SellerAdd1": "Seller's address line 1.",
    "SellerAdd2": "Seller's address line 2.",
    "SellerAdd3": "Seller's address line 3.",
    "SellerCountry": "The seller's country.",
    "SellerPinCode": "The seller's postal/PIN code.",
    "BrokerName": "Name of the broker involved in this transaction.",
    "BrokerAdd1": "Broker's address line 1.",
    "BrokerAdd2": "Broker's address line 2.",
    "BrokerAdd3": "Broker's address line 3.",
    "BrokerCountry": "The broker's country.",
    "BrokerPinCode": "The broker's postal/PIN code.",
    "AgencyComm": "Agency commission amount.",
    "AgencyCommCur": "Currency of the agency commission.",
    "AgencyCommRate": "Agency commission rate.",
    "LoadingCharges": "Loading charges amount.",
    "LoadingCur": "Currency of the loading charges.",
    "LoadingRate": "Loading charges rate.",
    "MiscCharges": "Miscellaneous charges amount.",
    "MiscCur": "Currency of the miscellaneous charges.",
    "MiscRate": "Miscellaneous charges rate.",
    "HssRate": "High Seas Sale rate -- the markup charged when goods are sold while still on the high seas, before import clearance.",
    "HssAmt": "High Seas Sale amount.",
    "DiscntNat": "Nature/category of the discount applied.",
    "DiscntRate": "Discount rate.",
    "DiscntAmt": "Discount amount.",
    "SVBRefNo": "Special Valuation Branch (SVB) reference/order number -- SVB examines related-party import pricing.",
    "SVBDate": "Date of that SVB reference/order.",
    "CustomHouse": "The customs house code for this filing.",
    "SVBonAssess": "SVB loading percentage applied on the assessable value.",
    "SVBonDuty": "SVB loading percentage applied on the duty.",
    "SVBFlag": "Y/N flag -- whether SVB loading applies to this invoice.",
    "SVBonAssessFlag": "Y/N flag -- whether SVB loading applies on assessable value.",
    "SVBonDutyFlag": "Y/N flag -- whether SVB loading applies on duty.",
    "Description": "General description for this invoice.",
    "NatureOfTransaction": "Category of the import transaction.",
    "TermsOfPayment": "Payment terms agreed for this invoice.",
    "BuyerSellerReln": "Whether the buyer and seller are related parties.",
    "ValMethodApplicable": "The customs valuation method applied (e.g. transaction value, computed value).",
    "ThirdPartyName": "Third party's name.",
    "ThirdPartyAdd1": "Third party's address line 1.",
    "ThirdPartyAdd2": "Third party's address line 2.",
    "ThirdPartyCity": "Third party's city.",
    "ThirdPartyCtrySubdv": "Third party's country subdivision (state/province).",
    "ThirdPartyCtry": "Third party's country.",
    "ThirdPartyPinCode": "Third party's postal/PIN code.",
    "TermsPlace": "The named place for the delivery term (e.g. \"CIF Mumbai\").",
    "AEOCode": "Authorized Economic Operator code, if claimed.",
    "AEOCtry": "Country code of the AEO scheme being claimed under.",
    "AEORole": "The AEO role/category applicable.",
    "BuyerSellerRelated": "Y/N flag -- whether the buyer and seller are related parties.",

    # -- ITEM (the Bill of Entry's own item table) --
    "ITEM_DESCRIPTION": "Commercial description of the goods for this item.",
    "GENERAL_DESCRIPTION": "A generic/tariff-level description of the goods.",
    "CHECKLIST_DESCRIPTION": "Description of the item as it appears on the customs checklist.",
    "TOTAL": "Total line amount for this item.",
    "MANUFACTURER_NAME": "Manufacturer's name.",
    "MODEL_NO": "Model number of the goods.",
    "ORIGIN_COUNTRY": "Country of origin of the goods.",
    "IGST_NOTN_NO": "IGST notification number claimed for this item.",
    "IGST_NOTN_SLNO": "Serial number within that IGST notification.",
    "IGST_IGST_FLAG": "Y/N flag -- whether IGST applies to this item.",
    "IGST_EXMP_NOTN_NO": "IGST exemption notification number claimed.",
    "IGST_EXMP_NOTN_SLNO": "Serial number within that IGST exemption notification.",
    "IGSTCESS_IGST_FLAG": "Y/N flag -- whether GST Compensation Cess applies to this item.",
    "IGSTCESS_NOTN_NO": "GST Compensation Cess notification number claimed.",
    "IGSTCESS_NOTN_SLNO": "Serial number within that GST Compensation Cess notification.",
    "IGSTCESS_EXMP_NOTN_NO": "GST Compensation Cess exemption notification number claimed.",
    "IGSTCESS_EXMP_NOTN_SLNO": "Serial number within that GST Compensation Cess exemption notification.",
    "BCD_NOTN": "Basic Customs Duty (BCD) exemption/concession notification number claimed.",
    "BCD_SLNO": "Serial number within that BCD notification.",
    "SWS_NOTN": "Social Welfare Surcharge (SWS) exemption notification number claimed.",
    "SWS_SLNO": "Serial number within that SWS notification.",
    "SAPTA_NOTN": "SAPTA preferential tariff notification number claimed.",
    "SAPTA_SLNO": "Serial number within that SAPTA notification.",
    "SCHEME_CODE": "The import scheme code this item is cleared under.",
    "SCHEME_NOTN_NO": "Scheme notification number.",
    "SCHEME_NOTN_SLNO": "Serial number within that scheme notification.",
    "EXEMPTION_FLAG": "Y/N flag -- whether a duty exemption is claimed for this item.",
    "LICENSE_REG_NO": "Import license registration number.",
    "REG_DATE": "Date of that license registration.",
    "REG_PORT": "Port at which that license was registered.",
    "ITEM_SLNO_LIC": "This item's own serial number as listed on the import license.",
    "PREVIOUS_SB_NO": "Reference shipping bill number (for a re-import).",
    "PREVIOUS_SB_DT": "Date of that reference shipping bill.",
    "PORT_OF_EXPORT": "Port from which the goods were originally exported (re-import context).",
    "INV_SLNO_SB": "That referenced shipping bill's own invoice serial number.",
    "ITEM_SLNO_SB": "That referenced shipping bill's own item serial number.",
    "REIMPORT_NOTN": "Re-import notification number claimed.",
    "REIMPORT_NOTN_SLNO": "Serial number within that re-import notification.",
    "SB_FREIGHT": "Freight as declared on the referenced shipping bill (re-import).",
    "SB_INSURANCE": "Insurance as declared on the referenced shipping bill (re-import).",
    "CUSTOM_DUTY": "Customs duty amount for this item.",
    "EXCISE_DUTY": "Excise duty amount for this item.",
    "SOURCE_CTRY": "Country the goods were sourced from.",
    "ITEM_ACCESSORIES": "Accessories accompanying this item.",
    "CVD_NOTN": "Countervailing Duty (CVD) notification number claimed.",
    "CVD_SLNO": "Serial number within that CVD notification.",
    "ADDCVD_NOTN": "Additional Countervailing Duty notification number claimed.",
    "ADDCVD_SLNO": "Serial number within that Additional CVD notification.",
    "AD1_NOTN": "First anti-dumping duty notification number claimed for this item.",
    "AD1_SLNO": "Serial number within that first anti-dumping notification.",
    "AD2_NOTN": "A second anti-dumping duty notification number claimed for this item.",
    "AD2_SLNO": "Serial number within that second anti-dumping notification.",
    "OTHER_NOTN": "Another duty notification number claimed for this item.",
    "OTHER_SLNO": "Serial number within that other notification.",
    "RSP_AMOUNT": "Retail Sale Price amount, for MRP-based valuation.",
    "RSP_FLAG": "Y/N flag -- whether RSP (MRP)-based assessment applies to this item.",
    "SVB_LOAD_ON_ASSVAL": "SVB loading percentage applied on this item's assessable value.",
    "SVB_LOAD_ON_DUTY": "SVB loading percentage applied on this item's duty.",
    "EXEMPTING_FLG": "Y/N flag -- whether an exemption applies to this item.",
    "LIC_URN": "License Unique Reference Number.",
    "CTH_UQC": "The unit of measure prescribed for this item's CTH/tariff classification.",
    "CTH_UQC_QUANTITY": "Quantity expressed in that CTH-prescribed unit.",
    "SIMS_INFO_CODE": "Steel Import Monitoring System (SIMS) information code.",
    "SIMS_INFO_TEXT": "Steel Import Monitoring System (SIMS) information text.",
    "HEALTHCESS_NOTN_NO": "Health Cess notification number claimed.",
    "HEALTHCESS_NOTN_SLNO": "Serial number within that Health Cess notification.",
    "ITEM_TEMPLATE": "Template/category classification of the item.",
    "COO_INFO_CODE": "Certificate of Origin information code.",
    "COO_INFO_TEXT": "Certificate of Origin information text.",
    "ACM_INFO_CODE": "UNCONFIRMED MEANING -- possibly an Accumulation/Cumulation (rules-of-origin) info code; verify against a real filed Bill of Entry.",
    "AIDC_DUTY_FG": "Y/N flag -- whether Agriculture Infrastructure and Development Cess (AIDC) applies to this item.",
    "RIS_INFO_CODE": "UNCONFIRMED MEANING -- a PGA information code; verify against a real filed Bill of Entry.",
    "DC_INFO_CODE": "UNCONFIRMED MEANING -- a PGA information code; verify against a real filed Bill of Entry.",
    "COWO_CONWO_INFO_QUALIFIER": "UNCONFIRMED MEANING -- likely a Single Window PGA declaration qualifier; verify against a real filed Bill of Entry.",
    "COWO_CONWO_INFO_CODE": "UNCONFIRMED MEANING -- likely a Single Window PGA declaration code; verify against a real filed Bill of Entry.",
    "COWO_CONWO_INFO_TEXT": "UNCONFIRMED MEANING -- likely a Single Window PGA declaration text; verify against a real filed Bill of Entry.",
    "COWO_CONWO_INFO_UQC": "UNCONFIRMED MEANING -- likely a Single Window PGA declaration unit of measure; verify against a real filed Bill of Entry.",
    "AIDC_DUTY_NOTN_NO": "AIDC duty notification number claimed.",
    "AIDC_DUTY_NOTN_SLNO": "Serial number within that AIDC duty notification.",

    # -- SW_INFOTYPE (Single Window PGA declaration) --
    "INFO_TYPE": "The Single Window information type code, as defined by the regulating PGA.",
    "INFO_QUALIFIER": "A qualifier further specifying that information type.",
    "INFO_CODE": "Code identifying the specific information item.",
    "INFO_TEXT": "The actual information/value being declared.",
    "INFO_MSR": "A numeric measure associated with this declaration.",
    "INFO_UQC": "Unit of measure for that numeric measure.",

    # -- SW_STATEMENT --
    "TYPE": "Category of statement being declared.",
    "CODE": "Code identifying that statement.",
    "TEXT": "Free-text content of that statement.",

    # -- SW_CONSTITUENT --
    "CONST_SRNO": "Serial number of this constituent entry.",
    "CONST_ELEMENT_NM": "Name of the constituent/ingredient element.",
    "CONST_ELEMENT_CODE": "Code identifying that constituent element.",
    "CONST_PERCENTAGE": "Percentage of this constituent in the product.",
    "CONST_YIELD_PERCENTAGE": "Yield percentage of this constituent.",
    "ACTIVE_INGREDIENT": "Y/N flag -- whether this is an active ingredient/constituent.",

    # -- SW_PRODUCTION --
    "BATCH_ID": "Production batch identifier.",
    "BATCH_QUANTITY": "Quantity produced in that batch.",
    "BATCH_UNIT": "Unit of that batch quantity.",
    "MANUFACTURER_DATE": "Date of manufacture.",
    "EXPIRY_DATE": "Expiry date.",
    "BEST_BEFORE_DATE": "Best-before date.",

    # -- ItemWiseSVB (Special Valuation Branch details, per item) --
    "SAVE_IN_ALL": "Y/N flag -- whether this SVB information applies to every item on this filing.",
    "REFERENCE_NO": "The SVB order/reference number.",
    "REFERENCE_DATE": "Date of that SVB order/reference.",
    "CUSTOMHOUSE_CODE": "Customs house code that issued the SVB order.",
    "SVB_LOAD_ON": "The basis the SVB loading is applied on (assessable value or duty).",
    "ASSESSABLE_VALUE": "Assessable value for this item.",
    "ASSESSABLE_LOAD_STATUS": "Status of the SVB loading applied to assessable value.",
    "DUTY": "Duty amount.",
    "DUTY_LOAD_STATUS": "Status of the SVB loading applied to duty.",

    # -- LICENSE --
    "LICENSE_NO": "License number.",
    "LICENSE_DATE": "License date.",
    "REGITRATION_NO": "License registration number (template's own spelling -- a typo for \"Registration\").",
    "REGITRATION_DATE": "Date of that license registration.",
    "REGITRATION_PORT": "Port at which that license was registered.",
    "LICENSE_SR_NO": "Serial number of this license entry.",
    "CIF_VALUE": "CIF value of the import being debited against this license.",
    "DEBIT_DUTY": "Duty amount debited against the license.",
    "DEBIT_QUANTITY": "Quantity debited against the license.",
    "DEBIT_QUANTITY_UNIT": "Unit of that debited quantity.",

    # -- Antidumping --
    # "Notification_No" and "Quantity" on THIS sheet specifically: see
    # _MANUAL_DESCRIPTIONS_BY_SHEET -- both reused elsewhere with a more
    # generic meaning (a notification number in general; a line item's own
    # quantity in general).
    "IsRemove": "Y/N flag -- whether this anti-dumping entry is to be excluded/not applicable.",
    "Antidumping_Type": "Category of anti-dumping duty applicable.",
    "CTH_Serial_No": "Serial number within that notification's CTH-wise (tariff-heading-wise) list.",
    "Supplier_Sl_No": "The specific supplier's serial number within the anti-dumping order (anti-dumping duties are often supplier-specific).",
    "IsDifferentFlag": "Y/N flag -- whether a different value/currency applies here than the invoice's own.",
    "IfDifferentCurrency": "The different currency, when IsDifferentFlag is set.",
    "SEAL_NO": "Seal number affixed to the container.",
    "Address1": "Supplier's address line 1.",
    "Address2": "Supplier's address line 2.",
    "Address3": "Supplier's address line 3.",
    "UNIT_PRICE": "Price per unit for this item.",
    "INV_NO": "Serial number of the invoice this row's data belongs to.",
    "ITEM_NO": "Serial number of the item this row's data belongs to.",
})

# Sheet-scoped overrides for a column name reused across templates with a
# GENUINELY different meaning per sheet -- checked before _MANUAL_DESCRIPTIONS'
# own name-only entry (see that dict's docstring above). Confirmed real
# collisions found when regenerating all 4 templates after adding this dict:
# the global entries for these 4 field names had been silently overwritten,
# template by template, by whichever _MANUAL_DESCRIPTIONS.update() call ran
# last, because the dict has no per-sheet scoping on its own.
_MANUAL_DESCRIPTIONS_BY_SHEET: dict[tuple[str, str], str] = {
    # "BE_No" -- export_xlsx's EXBOND_INFO sheet (ex-bond warehousing) vs
    # icafe's JOBWORK sheet (goods originally sent for job work) are
    # unrelated Bill of Entry references.
    ("EXBOND_INFO", "BE_No"): "The Bill of Entry number under which the goods were originally warehoused (for ex-bond export).",
    ("JOBWORK", "BE_No"): "Bill of Entry number of the original goods sent for job work.",
    # "Doc_Type" -- export_xlsx's E-SANCHIT sheet (an uploaded supporting
    # document's type) vs icafe's Container sheet (a container movement
    # document's type) are unrelated documents.
    ("E-SANCHIT", "Doc_Type"): "Type/category of the uploaded document (e.g. Certificate of Origin, Packing List, License copy).",
    ("Container", "Doc_Type"): "Type of document authorizing this container's movement (e.g. gate pass, transport permit).",
    # "Notification_No" -- icafe's JOBWORK sheet (a job-work notification)
    # vs import_excel_3's Antidumping sheet (an anti-dumping duty
    # notification) are unrelated notifications.
    ("JOBWORK", "Notification_No"): "The job-work notification number under which this export is made.",
    ("Antidumping", "Notification_No"): "Anti-dumping duty notification number.",
    # "Quantity" -- logi's own ITEMS sheet means this line item's ordinary
    # quantity (also the sensible default for _MANUAL_DESCRIPTIONS, since
    # that's what "Quantity" means on nearly every other sheet too);
    # import_excel_3's Antidumping sheet specifically means the quantity
    # subject to anti-dumping duty, a narrower concept that must not leak
    # into every other template's own "Quantity" column as the default.
    ("Antidumping", "Quantity"): "Quantity subject to anti-dumping duty.",
}

# ── Manual alias overrides for customs-specific abbreviations ──
# These are abbreviations/acronyms that can't be guessed from word-splitting.
_MANUAL_ALIASES: dict[str, list[str]] = {
    # Export template
    "Item_RITC":                ["HSN Code", "HSN/SAC Code", "RITC Code", "Tariff Code",
                                 "CTN", "RTC" ],
    "Item_Desc":                ["Description of Goods", "Item Description",
                                 "Product Description", "Material Description",
                                 "Mat Desc", "Product Name", "Goods Description"],
    "Item_Desc1":               ["Description of Goods", "Item Description",
                                 "Product Description", "Material Description",
                                 "Mat Desc", "Product Name", "Goods Description"],
    "Item_Gen_Desc":            ["General Description"],
    "Item_Ser_No":              ["Serial Number", "SL No", "S.No", "Item Number",
                                 "Serial No"],
    "SL_No":                    ["Serial Number", "S.No", "Item Number", "Item_Ser_No",
                                 "Serial No"],
    "Item_Unit1":               ["Unit of Measure", "UOM", "Unit", "UQC"],
    "Item_Unit2":               ["Secondary Unit", "Second UOM"],
    "Item_Unit":                ["Unit of Measure", "UOM", "Unit", "UQC"],
    "Item_Unit_Price":          ["Unit Price", "Rate", "Price per Unit",
                                 "Rate per Unit"],
    "Item_Qty":                 ["Quantity", "Qty", "Number of Items"],
    "Item_Country_Org":         ["Country of Origin", "Origin Country", "COO"],
    "Item_Scheme_Code":         ["Scheme Code", "Export Scheme Code"],
    "Item_SchemeCode":          ["Scheme Code", "Export Scheme Code"],
    "Itm_Manu_Code":           ["Manufacturer Code"],
    "Itm_Manu_Add1":           ["Manufacturer Address Line 1"],
    "Itm_Manu_Add2":           ["Manufacturer Address Line 2"],
    "Itm_Manu_City":           ["Manufacturer City"],
    "Itm_Manu_Pin":            ["Manufacturer PIN Code"],
    "Itm_Manu_Cntry":          ["Manufacturer Country"],
    "Itm_Manu_CntrySubDiv":    ["Manufacturer State/Province"],
    "Itm_Source_Cntry":        ["Source Country", "Country of Origin",
                                 "Country of Origin of Goods", "Origin Country",
                                 "Made In", "Origin"],
    "Itm_Transit_Cntry":       ["Transit Country", "Transshipment Country",
                                 "Via Country"],
    "ItmIGSTstatus":           ["IGST Status", "LUT", "Bond Status",
                                 "GST Payment Status"],
    "ItmTaxableVal":           ["Taxable Value", "Assessable Value",
                                 "Taxable Amount"],
    "Item_Taxable_Val":        ["Taxable Value", "Assessable Value",
                                 "Taxable Amount", "Extended Price",
                                 "Line Total", "Amount"],
    "ItmIGSTamt":              ["IGST Amount"],
    "ItmIGSTPer":              ["IGST Percentage", "IGST Rate"],
    "Item_PMV":                ["Previous Market Value", "PMV"],
    "Item_End_Use":            ["End Use", "EU Code", "End Use Code"],
    "Item_JNoti_No":           ["Joint Notification Flag"],
    "item_Cess":               ["Cess Flag"],
    "item_accessory":          ["Accessory Flag"],
    "item_thirdparty":         ["Third Party Flag"],
    "item_Quota":              ["Quota Flag"],
    "item_AR4":                ["AR4 Flag"],
    "Item_Reward":             ["Reward Flag", "RoDTEP Flag", "RoDTEP Declaration",
                                 "RoDTEP", "MEIS", "Remission of Duties and Taxes on Exported Products",
                                 "Reward Scheme", "Export Reward"],
    "Item_STR":                ["STR Flag"],
    "Item_BCD_Ntfn":           ["BCD Notification Number",
                                 "Basic Customs Duty Notification"],
    "Item_BCD_Ntfn_SLNo":      ["BCD Notification Serial Number"],
    "Item_CVD_Ntfn":           ["CVD Notification Number"],
    "Item_CVD_Ntfn_SLNo":      ["CVD Notification Serial Number"],
    "PER":                     ["Per Unit", "Price Per"],
    "ItmHawb":                 ["HAWB Number", "House Airway Bill"],
    "ItmTotpkg":               ["Total Packages", "Total Cases",
                                 "No. of Packages", "Number of Packages"],
    "Printed":                 ["Print Status"],
    "FTA Code":                ["Free Trade Agreement Code",
                                 "Preferential Tariff Code", "FTA"],
    "State Code":              ["State Code", "Exporter State Code", "State Code : MH(12)",
                                 "State Code :", "State Code Number", "State", "State / State Code"],
    "District Code":           ["District Code", "District Code Number", "District"],
    "Qty Tariff":              ["Quantity for Tariff", "Tariff Quantity"],
    "Unit Tariff":             ["Unit for Tariff", "Tariff Unit"],
    # Import template extras
    "Item_SWC_Ntfn":           ["SWC Notification"],
    "Item_SWC_Ntfn_SlNo":      ["SWC Notification Serial"],
    # -- Import- LOGYSIS TEMPLATE.csv ("logysis_import") -- draft, see
    # _MANUAL_DESCRIPTIONS' own note on this template above.
    "Product Desc":            ["Product Description", "Description", "Description of Goods",
                                 "Item Description", "Goods Description"],
    "CTH":                     ["Customs Tariff Heading", "Tariff Heading", "HSN", "HSN Code"],
    "CETH":                    ["Central Excise Tariff Heading"],
    "Country of origin":       ["Country of Origin", "Origin Country", "COO", "Made In"],
}


def _auto_alias(col_name: str) -> str:
    """
    Generate a human-readable alias by splitting on underscores, expanding
    common abbreviations, and title-casing.
    """
    _ABBREVS = {
        "ser": "Serial", "no": "Number", "desc": "Description",
        "qty": "Quantity", "org": "Origin", "cntry": "Country",
        "manu": "Manufacturer", "add": "Address", "itm": "Item",
        "sl": "Serial", "ntfn": "Notification", "mfd": "Manufactured",
        "per": "Percentage", "val": "Value", "amt": "Amount",
        "regn": "Registration", "lic": "License",
    }
    parts = col_name.replace("_", " ").split()
    expanded = [_ABBREVS.get(p.lower(), p.capitalize()) for p in parts]
    return " ".join(expanded)


def _infer_type(col_name: str) -> str:
    name = col_name.lower()
    # Force string for known Y/N flags, codes, and text fields
    if name in _STRING_FORCE:
        return "string"
    if any(h in name for h in _DATE_HINTS):
        return "string"  # dates kept as ISO strings, not JSON numbers
    if any(h in name for h in _NUMBER_HINTS):
        return "number"
    return "string"


def _build_sheet_entry(headers: list[Any], sheet_name: str | None = None) -> dict[str, Any]:
    """One sheet's worth of {"fields", "types", "aliases", "descriptions",
    "link_field"} -- shared by every format load_template_schema reads
    (.xlsx/.xls/.csv), since all three ultimately reduce to the same thing:
    a flat list of column-header strings from one header row.

    `aliases` maps each column name to a list of common invoice labels
    that the LLM should recognise as that column.

    `descriptions` maps each column name to a hand-written explanation of
    what it MEANS -- "" for a field with none. Checks
    _MANUAL_DESCRIPTIONS_BY_SHEET first, by `(sheet_name, field)`, before
    falling back to _MANUAL_DESCRIPTIONS' name-only entry: a column name
    reused across templates with a genuinely different meaning per sheet
    (confirmed real cases: "BE_No", "Doc_Type", "Notification_No",
    "Quantity" -- see that dict's own comments) needs the sheet-specific
    text, not whichever template's entry happened to be written last into
    the shared, name-only dict.

    `link_field` is whichever column ties a sheet's rows back to a
    specific ITEM row (most sheets use "Item_No"; ITEM itself uses
    "Item_Ser_No"), so extracted rows can be joined later.
    """
    headers = [str(h).strip() for h in headers if h is not None and str(h).strip()]
    fields = [h for h in headers if h.lower() not in _SYSTEM_FIELDS]
    types = {h: _infer_type(h) for h in fields}

    # Build aliases: manual overrides first, then auto-generate
    aliases: dict[str, list[str]] = {}
    for h in fields:
        if h in _MANUAL_ALIASES:
            aliases[h] = _MANUAL_ALIASES[h]
        else:
            aliases[h] = [_auto_alias(h)]

    descriptions = {
        h: _MANUAL_DESCRIPTIONS_BY_SHEET.get((sheet_name, h)) or _MANUAL_DESCRIPTIONS.get(h, "")
        for h in fields
    }

    link_field = next(
        (h for h in fields if h.replace(" ", "_").lower() in
         ("item_no", "item_ser_no", "sl_no")),
        None,
    )

    return {
        "fields": fields, "types": types, "aliases": aliases,
        "descriptions": descriptions, "link_field": link_field,
    }


def field_description_text(field: str, aliases: dict[str, list[str]], descriptions: dict[str, str] | None = None) -> str | None:
    """
    Combines a field's hand-written meaning (_MANUAL_DESCRIPTIONS, via
    `descriptions`) with its alias list into the one description string
    actually shown to the LLM -- the single place this combination happens,
    used identically by chunked_extraction.py's Pydantic models/prompts,
    models.py's validation models, and tools/generate_template_models.py's
    generated files, so a field reads the same wherever it shows up.
    Returns None (no description at all) only when neither is present.
    """
    meaning = (descriptions or {}).get(field) or ""
    als = aliases.get(field) or []
    alias_text = f"Also printed as: {', '.join(als)}" if als else ""
    parts = [p for p in (meaning, alias_text) if p]
    return " ".join(parts) if parts else None


def _load_xlsx_schema(path: Path) -> dict[str, dict[str, Any]]:
    wb = openpyxl.load_workbook(path, data_only=True)
    schema: dict[str, dict[str, Any]] = {}
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        headers = [c.value for c in ws[1] if c.value is not None]
        schema[sheet_name] = _build_sheet_entry(headers, sheet_name=sheet_name)
    return schema


def _load_xls_schema(path: Path) -> dict[str, dict[str, Any]]:
    """Legacy binary Excel (.xls, a Composite Document File / OLE2
    container) -- openpyxl can't open these at all; xlrd (2.x, which
    dropped .xlsx support but kept .xls) can."""
    import xlrd
    wb = xlrd.open_workbook(str(path))
    schema: dict[str, dict[str, Any]] = {}
    for sheet_name in wb.sheet_names():
        ws = wb.sheet_by_name(sheet_name)
        if ws.nrows == 0:
            continue
        schema[sheet_name] = _build_sheet_entry(ws.row_values(0), sheet_name=sheet_name)
    return schema


def _load_csv_schema(path: Path) -> dict[str, dict[str, Any]]:
    """A .csv template has no sheets at all -- it's one flat table, so this
    returns a single-entry dict under templates_config._CSV_SHEET_NAME
    ("ITEMS"), not imported from there (same "restated, not imported, to
    avoid a dependency neither module otherwise needs" reasoning as
    llm_extract.py's own field-mapping tables) -- keep both in sync by
    hand if that name ever changes."""
    import csv
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        headers = next(csv.reader(f), [])
    return {"ITEMS": _build_sheet_entry(headers, sheet_name="ITEMS")}


def load_template_schema(xlsx_path: str | Path) -> dict[str, dict[str, Any]]:
    """
    Returns, per sheet:
        {
          "ITEM": {
             "fields": ["Item_Ser_No", "Item_Desc", ...],
             "types":  {"Item_Ser_No": "string", "Item_Qty": "number", ...},
             "aliases": {"Item_Ser_No": ["Serial Number", "S.No", ...], ...},
             "link_field": "Item_No" | "Item_Ser_No" | None
          },
          ...
        }

    Dispatches on the file's extension: .xlsx (openpyxl, every sheet),
    .xls (xlrd, every sheet -- a legacy binary format openpyxl can't
    read), or .csv (one flat table with no sheets, so this returns a
    single entry -- see _load_csv_schema).
    """
    path = Path(xlsx_path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv_schema(path)
    if suffix == ".xls":
        return _load_xls_schema(path)
    return _load_xlsx_schema(path)


if __name__ == "__main__":
    import json, sys
    path = sys.argv[1] if len(sys.argv) > 1 else "templates/EXP_TEMPLET.xlsx"
    s = load_template_schema(path)
    print(json.dumps(s, indent=2))

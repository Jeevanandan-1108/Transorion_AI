from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class LogiItemsChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    inv_no: Optional[str] = Field(default=None, alias='Inv No', description="The supplier's own commercial invoice number for this shipment. Also printed as: Inv Number")
    date: Optional[str] = Field(default=None, alias='Date', description="The commercial invoice's own date. Also printed as: Date")
    toi: Optional[str] = Field(default=None, alias='TOI', description='UNCONFIRMED MEANING -- likely "Terms of Invoice" (e.g. an Incoterm such as FOB/CIF/EXW) or possibly "Type of Invoice"; verify against a real filed BOE before trusting this. Also printed as: Toi')
    product_desc: Optional[str] = Field(default=None, alias='Product Desc', description='The commercial description of the goods for this specific line item. Also printed as: Product Description, Description, Description of Goods, Item Description, Goods Description')
    quantity: Optional[str] = Field(default=None, alias='Quantity', description='The quantity of this line item. Also printed as: Quantity')
    unit: Optional[str] = Field(default=None, alias='Unit', description='Unit of that quantity. Also printed as: Unit')
    currency: Optional[str] = Field(default=None, alias='Currency', description='The invoice currency. Also printed as: Currency')
    rate: Optional[float | str] = Field(default=None, alias='Rate', description='The unit price (price per single unit of Quantity) for this item. Also printed as: Rate')
    cth: Optional[str] = Field(default=None, alias='CTH', description='Customs Tariff Heading -- the HS/ITC-HS tariff classification code for this item. Also printed as: Customs Tariff Heading, Tariff Heading, HSN, HSN Code')
    basic_notification: Optional[str] = Field(default=None, alias='Basic notification', description='Basic Customs Duty (BCD) exemption/concession notification number claimed for this item. Also printed as: Basic Notification')
    sr_no: Optional[str] = Field(default=None, alias='Sr no', description="AMBIGUOUS -- this exact column header is reused 3 times in the source template (after Basic notification, Excise notification, and SAD notification), each meaning THAT notification's own serial number. They cannot be told apart or filled independently until the source template's columns are renamed to be distinct; see field_naming discussion. Until then, treat any value here as unreliable. Also printed as: Sr Number")
    ceth: Optional[str] = Field(default=None, alias='CETH', description='Central Excise Tariff Heading -- the classification code under the Central Excise Tariff (legacy/pre-GST, still relevant for excise-levied goods like petroleum and tobacco). Also printed as: Central Excise Tariff Heading')


class LogiItemsChunk1Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    excise_notification: Optional[str] = Field(default=None, alias='Excise notification', description='Central Excise duty exemption/concession notification number claimed. Also printed as: Excise Notification')
    sr_no: Optional[str] = Field(default=None, alias='Sr no', description="AMBIGUOUS -- this exact column header is reused 3 times in the source template (after Basic notification, Excise notification, and SAD notification), each meaning THAT notification's own serial number. They cannot be told apart or filled independently until the source template's columns are renamed to be distinct; see field_naming discussion. Until then, treat any value here as unreliable. Also printed as: Sr Number")
    mrp_sr_no: Optional[str] = Field(default=None, alias='Mrp Sr no', description="Serial number within the MRP (Maximum Retail Price)-based valuation notification's own list of entries, for goods assessed on MRP rather than transaction value. Also printed as: Mrp Sr Number")
    amount: Optional[float | str] = Field(default=None, alias='Amount', description='The total line amount for this item (Quantity x Rate). Also printed as: Amount')
    sad_notification: Optional[str] = Field(default=None, alias='SAD notification', description='Special Additional Duty (SAD) exemption notification number claimed -- a legacy duty largely subsumed into GST/IGST post-2017, but the field persists in some BOE formats. Also printed as: Sad Notification')
    sr_no_2: Optional[str] = Field(default=None, alias='Sr no', description="AMBIGUOUS -- this exact column header is reused 3 times in the source template (after Basic notification, Excise notification, and SAD notification), each meaning THAT notification's own serial number. They cannot be told apart or filled independently until the source template's columns are renamed to be distinct; see field_naming discussion. Until then, treat any value here as unreliable. Also printed as: Sr Number")
    exim_code: Optional[str] = Field(default=None, alias='Exim Code', description='Export-Import (EXIM) policy code, sometimes used alongside/instead of the IEC. Also printed as: Exim Code')
    scheme_notn: Optional[str] = Field(default=None, alias='Scheme Notn', description='Scheme notification number -- e.g. the EPCG/Advance Authorization/DFIA export-promotion scheme notification under which this item is being cleared. Also printed as: Scheme Notn')
    scheme_srno: Optional[str] = Field(default=None, alias='Scheme SrNo', description="Serial number within that scheme notification's own list of entries. Also printed as: Scheme Srno")
    generic_description: Optional[str] = Field(default=None, alias='Generic Description', description='A generic/tariff-level description of the goods, as opposed to the specific commercial description in Product Desc -- used for customs classification purposes. Also printed as: Generic Description')
    brand: Optional[str] = Field(default=None, alias='BRAND', description='Brand name of the goods. Also printed as: Brand')
    model: Optional[str] = Field(default=None, alias='Model', description='Model number or variant of the goods. Also printed as: Model')


class LogiItemsChunk2Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    end_use: Optional[str] = Field(default=None, alias='END USE', description="End-use code/declaration, where a concessional duty rate depends on the goods' declared end use. Also printed as: End Use")
    educessnotnno: Optional[str] = Field(default=None, alias='EduCessNotnNo', description='Education Cess notification number (legacy surcharge on customs duty, phased out around 2015 -- the field persists in some BOE formats). Also printed as: Educessnotnno')
    educessnotnsrno: Optional[str] = Field(default=None, alias='EduCessNotnSrNo', description='Serial number within that Education Cess notification. Also printed as: Educessnotnsrno')
    sheducessnotnno: Optional[str] = Field(default=None, alias='SHEduCessNotnNo', description='Secondary and Higher Education Cess notification number (a second tier of the legacy cess above). Also printed as: Sheducessnotnno')
    sheducessnotnsrno: Optional[str] = Field(default=None, alias='SHEduCessNotnSrNo', description='Serial number within that Secondary and Higher Education Cess notification. Also printed as: Sheducessnotnsrno')
    cexeducessnotnno: Optional[str] = Field(default=None, alias='CEXEduCessNotnNo', description='Central Excise Education Cess notification number. Also printed as: Cexeducessnotnno')
    cexeducessnotnsrno: Optional[str] = Field(default=None, alias='CEXEduCessNotnSrNo', description='Serial number within that Central Excise Education Cess notification. Also printed as: Cexeducessnotnsrno')
    country_of_origin: Optional[str] = Field(default=None, alias='Country of origin', description='The country where the goods were manufactured/produced. Also printed as: Country of Origin, Origin Country, COO, Made In')
    rsp_notf: Optional[str] = Field(default=None, alias='RSP Notf', description='Retail Sale Price notification number, for goods assessed on MRP under the Legal Metrology (Packaged Commodities) framework. Also printed as: Rsp Notf')
    sr_no: Optional[str] = Field(default=None, alias='Sr. no.', description='Serial number within the RSP Notf (Retail Sale Price notification) list -- a separate, near-duplicate header (with periods) from the "Sr no" columns above; not ambiguous on its own, but easy to confuse with them. Also printed as: Sr. No.')
    sapta_notif_no: Optional[str] = Field(default=None, alias='Sapta Notif No', description='SAPTA (South Asian Preferential Trade Arrangement) preferential tariff notification number claimed. Also printed as: Sapta Notif Number')
    sapta_sr_no: Optional[str] = Field(default=None, alias='SAPTA Sr No', description='Serial number within that SAPTA notification. Also printed as: Sapta Sr Number')


class LogiItemsChunk3Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    manufacturer: Optional[str] = Field(default=None, alias='Manufacturer', description='Name of the actual manufacturer of the goods. Also printed as: Manufacturer')
    address: Optional[str] = Field(default=None, alias='Address', description="The manufacturer's address. Also printed as: Address")
    mrp_status: Optional[str] = Field(default=None, alias='MRP Status', description='Y/N flag -- whether this item is valued using MRP-based (Maximum Retail Price) assessment. Also printed as: Mrp Status')
    abatement_rate: Optional[float | str] = Field(default=None, alias='Abatement Rate', description="The abatement percentage allowed on MRP when computing this item's assessable value. Also printed as: Abatement Rate")
    igst_notn_no: Optional[str] = Field(default=None, alias='IGST Notn No', description='Integrated GST (IGST) exemption/concession notification number claimed on import. Also printed as: Igst Notn Number')
    igst_srno: Optional[str] = Field(default=None, alias='IGST SrNo', description='Serial number within that IGST notification. Also printed as: Igst Srno')
    gst_ccess_notn_no: Optional[str] = Field(default=None, alias='GST CCESS Notn No', description='GST Compensation Cess notification number claimed -- applies to luxury/sin goods such as tobacco, coal, and automobiles. Also printed as: Gst Ccess Notn Number')
    gst_ccess_srno: Optional[str] = Field(default=None, alias='GST CCESS SrNo', description='Serial number within that GST Compensation Cess notification. Also printed as: Gst Ccess Srno')
    igst_exemp_notn_type: Optional[str] = Field(default=None, alias='IGST Exemp Notn Type', description='Type/category of the IGST exemption notification claimed for this item. Also printed as: Igst Exemp Notn Type')
    igst_exemp_notn_no: Optional[str] = Field(default=None, alias='IGST Exemp Notn No', description='IGST exemption notification number (as distinct from a concession under IGST Notn No). Also printed as: Igst Exemp Notn Number')
    igst_exemp_srno: Optional[str] = Field(default=None, alias='IGST Exemp SrNo', description='Serial number within that IGST exemption notification. Also printed as: Igst Exemp Srno')
    gst_ccess_exemp_notntype: Optional[str] = Field(default=None, alias='GST CCESS Exemp NotnType', description='Type/category of the GST Compensation Cess exemption notification claimed. Also printed as: Gst Ccess Exemp Notntype')


class LogiItemsChunk4Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    gst_ccess_exemp_notn_no: Optional[str] = Field(default=None, alias='GST CCESS Exemp Notn No', description='GST Compensation Cess exemption notification number. Also printed as: Gst Ccess Exemp Notn Number')
    gst_ccess_exemp_srno: Optional[str] = Field(default=None, alias='GST CCESS Exemp SrNo', description='Serial number within that GST Compensation Cess exemption notification. Also printed as: Gst Ccess Exemp Srno')
    manufacturer_cntry: Optional[str] = Field(default=None, alias='Manufacturer Cntry', description='Country of the manufacturer. Also printed as: Manufacturer Country')
    manufacturer_state: Optional[str] = Field(default=None, alias='Manufacturer State', description="State/province of the manufacturer, within the manufacturer's own country. Also printed as: Manufacturer State")
    manuf_postal_code: Optional[str] = Field(default=None, alias='Manuf Postal Code', description="The manufacturer's postal/ZIP code. Also printed as: Manuf Postal Code")
    sws_notn_no: Optional[str] = Field(default=None, alias='SWS Notn No', description='Social Welfare Surcharge (SWS) exemption notification number claimed. Also printed as: Sws Notn Number')
    sws_notn_srno: Optional[str] = Field(default=None, alias='SWS Notn SrNo', description='Serial number within that Social Welfare Surcharge notification. Also printed as: Sws Notn Srno')
    duty_exemption_type: Optional[float | str] = Field(default=None, alias='Duty Exemption Type', description='The category/type of duty exemption claimed for this item overall. Also printed as: Duty Exemption Type')
    regn_no: Optional[str] = Field(default=None, alias='Regn No.', description='Registration number of the import license/authorization covering this item. Also printed as: Registration No.')
    regn_date: Optional[str] = Field(default=None, alias='Regn Date', description='Date that registration/license was registered. Also printed as: Registration Date')
    regn_port: Optional[str] = Field(default=None, alias='Regn Port', description='The port at which that registration/license was registered. Also printed as: Registration Port')
    item_sno_in_lic: Optional[str] = Field(default=None, alias='Item SNo. In Lic.', description="This item's own serial number as listed within the import license/authorization document. Also printed as: Item Sno. In Lic.")


class LogiItemsChunk5Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    product_material_code: Optional[str] = Field(default=None, alias='Product Material Code', description='An internal or customs material/commodity code for the product. Also printed as: Product Material Code')
    type_of_item: Optional[str] = Field(default=None, alias='Type Of Item', description='Category/type classification of the item. Also printed as: Type Of Item')
    source_country: Optional[str] = Field(default=None, alias='Source Country', description='Country the goods were shipped/sourced from (may differ from Country of origin if transshipped). Also printed as: Source Country')
    transit_country: Optional[str] = Field(default=None, alias='Transit Country', description='Country through which the goods transited en route to India. Also printed as: Transit Country')
    sqc_qty: Optional[float | str] = Field(default=None, alias='SQC_Qty', description='Statistical Quantity Code quantity -- the quantity expressed in the standard unit used for customs trade statistics, distinct from the commercial Quantity/Unit above. Also printed as: Sqc Quantity')
    consigner_name: Optional[str] = Field(default=None, alias='Consigner Name', description='Name of the consignor -- the party shipping the goods (often the exporter/manufacturer). Also printed as: Consigner Name')
    consigner_address: Optional[str] = Field(default=None, alias='Consigner Address', description="The consignor's address. Also printed as: Consigner Address")
    consigner_city: Optional[str] = Field(default=None, alias='Consigner City', description="The consignor's city. Also printed as: Consigner City")
    consigner_country: Optional[str] = Field(default=None, alias='Consigner Country', description="The consignor's country. Also printed as: Consigner Country")
    sims_category: Optional[str] = Field(default=None, alias='SIMS Category', description='Steel Import Monitoring System (SIMS) category code, applicable to steel product imports. Also printed as: Sims Category')
    sims_code: Optional[str] = Field(default=None, alias='SIMS Code', description='SIMS registration code/number for this import. Also printed as: Sims Code')
    custom_health_cess_notn_no: Optional[str] = Field(default=None, alias='Custom Health Cess Notn No', description='Health Cess notification number -- Health Cess is levied on certain medical device imports. Also printed as: Custom Health Cess Notn Number')


class LogiItemsChunk6Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    custom_health_cess_notn_srno: Optional[str] = Field(default=None, alias='Custom Health Cess Notn SrNo', description='Serial number within that Health Cess notification. Also printed as: Custom Health Cess Notn Srno')
    coo_no: Optional[str] = Field(default=None, alias='COO_No', description='Certificate of Origin (COO) number. Also printed as: Coo Number')
    coo_date_of_issue: Optional[str] = Field(default=None, alias='COO_Date of Issue', description='Date the Certificate of Origin was issued. Also printed as: Coo Date Of Issue')
    coo_issuing_country: Optional[str] = Field(default=None, alias='COO_Issuing Country', description='The country that issued the Certificate of Origin. Also printed as: Coo Issuing Country')
    coo_origin_criteria: Optional[str] = Field(default=None, alias='COO_Origin Criteria', description='The origin criteria code under which the goods qualify as originating under the relevant trade agreement (e.g. wholly obtained, change in tariff heading, regional value content). Also printed as: Coo Origin Criteria')
    coo_origin_criteria_remarks: Optional[str] = Field(default=None, alias='COO_Origin Criteria remarks', description='Additional remarks/explanation on the origin criteria claimed. Also printed as: Coo Origin Criteria Remarks')
    coo_accumulation_cumulation_status: Optional[str] = Field(default=None, alias='COO_Accumulation/Cumulation Status', description='Whether cumulation/accumulation provisions (combining originating materials from multiple FTA partner countries) were applied in determining origin. Also printed as: Coo Accumulation/cumulation Status')
    foc_item: Optional[str] = Field(default=None, alias='FOC Item', description='Y/N flag -- Free of Cost item (e.g. a warranty replacement or free sample supplied without charge). Also printed as: Foc Item')
    aidc_levy_notification_no: Optional[str] = Field(default=None, alias='AIDC Levy Notification No', description='Agriculture Infrastructure and Development Cess (AIDC) levy notification number. Also printed as: Aidc Levy Notification Number')
    aidc_levy_notification_sr_no: Optional[str] = Field(default=None, alias='AIDC Levy Notification Sr.No.', description='Serial number within that AIDC levy notification. Also printed as: Aidc Levy Notification Sr.no.')
    aidc_exemption_notification_no: Optional[str] = Field(default=None, alias='AIDC Exemption Notification No.', description='AIDC exemption notification number claimed. Also printed as: Aidc Exemption Notification No.')
    aidc_exemption_notification_sr_no: Optional[str] = Field(default=None, alias='AIDC Exemption Notification Sr.No.', description='Serial number within that AIDC exemption notification. Also printed as: Aidc Exemption Notification Sr.no.')


class LogiItemsChunk7Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    aidcnotn_excise: Optional[str] = Field(default=None, alias='AIDCNotn_Excise', description='AIDC notification number applicable under the Central Excise regime (e.g. for petroleum products, where AIDC is levied via excise rather than customs). Also printed as: Aidcnotn Excise')
    adicnotnsr_excise: Optional[str] = Field(default=None, alias='ADICNotnSr_Excise', description='Serial number within that excise-side AIDC notification. Also printed as: Adicnotnsr Excise')
    previous_be_no: Optional[str] = Field(default=None, alias='Previous BE No.', description='Bill of Entry number of a prior, related import (e.g. a linked earlier consignment or re-import). Also printed as: Previous Be No.')
    previous_be_date: Optional[str] = Field(default=None, alias='Previous BE Date', description='Date of that previous Bill of Entry. Also printed as: Previous Be Date')
    previous_be_igm_no: Optional[str] = Field(default=None, alias='Previous BE IGM No.', description='Import General Manifest (IGM) number associated with that previous Bill of Entry. Also printed as: Previous Be Igm No.')
    previous_be_igm_date: Optional[str] = Field(default=None, alias='Previous BE IGM Date', description='Date of that previous Import General Manifest. Also printed as: Previous Be Igm Date')
    previous_be_currency: Optional[str] = Field(default=None, alias='Previous BE Currency', description='The invoice currency recorded on that previous Bill of Entry. Also printed as: Previous Be Currency')
    previous_be_unit_price: Optional[float | str] = Field(default=None, alias='Previous BE Unit Price', description='The unit price recorded on that previous Bill of Entry (for comparison, e.g. in re-import cases). Also printed as: Previous Be Unit Price')
    previous_be_custom_house: Optional[str] = Field(default=None, alias='Previous BE Custom House', description='The customs house (port/ICD) where that previous Bill of Entry was filed. Also printed as: Previous Be Custom House')
    aggregate_duty_notification_no: Optional[float | str] = Field(default=None, alias='Aggregate Duty Notification No.', description='Notification number under which an aggregate (combined) duty rate was claimed for this item. Also printed as: Aggregate Duty Notification No.')
    aggregate_duty_notification_sr_no: Optional[float | str] = Field(default=None, alias='Aggregate Duty Notification Sr.No.', description='Serial number within that aggregate duty notification. Also printed as: Aggregate Duty Notification Sr.no.')
    coo_tariff_shift: Optional[str] = Field(default=None, alias='COO_Tariff Shift', description='Whether origin was determined via a "tariff shift" (change in tariff classification) rule-of-origin method. Also printed as: Coo Tariff Shift')


class LogiItemsChunk8Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    coo_retroactive_issuance_status: Optional[str] = Field(default=None, alias='COO_Retroactive Issuance Status', description='Whether the Certificate of Origin was issued retroactively (after the goods were shipped) rather than at the time of shipment. Also printed as: Coo Retroactive Issuance Status')
    coo_direct_consignment_status: Optional[str] = Field(default=None, alias='COO_Direct Consignment Status', description='Whether the goods meet the "direct consignment" rule of origin (shipped directly from the originating country without further processing elsewhere en route). Also printed as: Coo Direct Consignment Status')
    gst_comp_cess_saleprice_inr: Optional[float | str] = Field(default=None, alias='GST_Comp_Cess_SalePrice_INR', description='The sale price in INR used as the basis for computing GST Compensation Cess, for goods (e.g. tobacco) where the cess is based on retail sale price rather than assessable value. Also printed as: Gst Comp Cess Saleprice Inr')
    cvd_notn: Optional[str] = Field(default=None, alias='CVD_Notn', description='Countervailing Duty (CVD) notification number claimed (anti-subsidy duty on this item). Also printed as: Cvd Notn')
    cvd_slno: Optional[str] = Field(default=None, alias='CVD_SLNo', description='Serial number within that CVD notification. Also printed as: Cvd Slno')
    cvd_rate: Optional[float | str] = Field(default=None, alias='CVD_Rate', description='The Countervailing Duty rate (percentage or amount) applicable to this item. Also printed as: Cvd Rate')
    cvd_calc: Optional[str] = Field(default=None, alias='CVD_Calc', description='The calculated Countervailing Duty amount for this item. Also printed as: Cvd Calc')
    cvd_itemslno: Optional[str] = Field(default=None, alias='CVD_ItemSLNo', description="This item's own serial number within the CVD notification/order's item list. Also printed as: Cvd Itemslno")
    cvd_suppslno: Optional[str] = Field(default=None, alias='CVD_SuppSLNo', description="The supplier's serial number within the CVD notification/order -- anti-dumping/CVD orders often list specific supplier-wise rates. Also printed as: Cvd Suppslno")
    coo_itemsrnocert: Optional[str] = Field(default=None, alias='COO_ItemSrNoCert', description="This item's own serial number as listed on the Certificate of Origin certificate. Also printed as: Coo Itemsrnocert")


CHUNK_MODELS = {
    ('ITEMS', 0): LogiItemsChunk0Row,
    ('ITEMS', 1): LogiItemsChunk1Row,
    ('ITEMS', 2): LogiItemsChunk2Row,
    ('ITEMS', 3): LogiItemsChunk3Row,
    ('ITEMS', 4): LogiItemsChunk4Row,
    ('ITEMS', 5): LogiItemsChunk5Row,
    ('ITEMS', 6): LogiItemsChunk6Row,
    ('ITEMS', 7): LogiItemsChunk7Row,
    ('ITEMS', 8): LogiItemsChunk8Row,
}


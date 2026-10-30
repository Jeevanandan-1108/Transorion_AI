from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class ExportExcGeneralChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    transportmode: Optional[str] = Field(default=None, alias='TransportMode', description='Mode of transport for this shipment (e.g. Sea, Air, Road, Rail, ICD). Also printed as: Transportmode')
    customhousecode: Optional[str] = Field(default=None, alias='CustomHouseCode', description='The customs house/port code where this Shipping Bill is filed. Also printed as: Customhousecode')
    sb_type: Optional[str] = Field(default=None, alias='SB_Type', description='Shipping Bill type/category (e.g. Free Shipping Bill, Drawback, DEEC, EPCG, EOU, Coastal). Also printed as: Sb Type')
    is_sez_job: Optional[str] = Field(default=None, alias='Is_SEZ_Job', description='Y/N flag -- whether this shipment relates to an SEZ (Special Economic Zone) job-work transaction. Also printed as: Is Sez Job')
    loading_port: Optional[str] = Field(default=None, alias='Loading_Port', description='The port where the goods are loaded onto the vessel/aircraft. Also printed as: Loading Port')
    exporter_name: Optional[str] = Field(default=None, alias='Exporter_Name', description='Name of the exporter. Also printed as: Exporter Name')
    branchname: Optional[str] = Field(default=None, alias='BranchName', description="The exporter's branch name/code filing this shipment. Also printed as: Branchname")
    exporterrefno: Optional[str] = Field(default=None, alias='ExporterRefNo', description="The exporter's own internal reference number for this shipment. Also printed as: Exporterrefno")
    exporterrefdate: Optional[str] = Field(default=None, alias='ExporterRefDate', description='Date of that exporter reference. Also printed as: Exporterrefdate')
    exportertype: Optional[str] = Field(default=None, alias='ExporterType', description='Category of exporter (e.g. Manufacturer, Merchant, EOU, SEZ unit). Also printed as: Exportertype')
    isgrwaived: Optional[str] = Field(default=None, alias='IsGRWaived', description='Y/N flag -- whether the GR (Guaranteed Remittance) waiver applies, exempting this shipment from RBI foreign exchange declaration. Also printed as: Isgrwaived')
    gr_rbiwaiver_no: Optional[str] = Field(default=None, alias='GR_RBIWaiver_No', description='The RBI GR waiver reference number, when claimed. Also printed as: Gr Rbiwaiver Number')


class ExportExcGeneralChunk1Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    gr_rbiwaiver_date: Optional[str] = Field(default=None, alias='GR_RBIWaiver_date', description='Date of that RBI GR waiver. Also printed as: Gr Rbiwaiver Date')
    ad_code: Optional[str] = Field(default=None, alias='AD_Code', description='Authorized Dealer (bank) code through which export proceeds will be realized. Also printed as: Ad Code')
    consignee_name: Optional[str] = Field(default=None, alias='Consignee_name', description='Name of the consignee (the overseas receiving party). Also printed as: Consignee Name')
    consignee_address: Optional[str] = Field(default=None, alias='Consignee_address', description="The consignee's address. Also printed as: Consignee Address")
    consignee_country_code: Optional[str] = Field(default=None, alias='Consignee_Country_Code', description="The consignee's country code. Also printed as: Consignee Country Code")
    isbuyerotherthanconsignee: Optional[str] = Field(default=None, alias='IsBuyerOtherThanConsignee', description='Y/N flag -- whether the buyer is a different party than the consignee. Also printed as: Isbuyerotherthanconsignee')
    notify_name: Optional[str] = Field(default=None, alias='Notify_name', description='Name of the notify party (to be notified on arrival, per the shipping documents). Also printed as: Notify Name')
    notify_address: Optional[str] = Field(default=None, alias='Notify_address', description="The notify party's address. Also printed as: Notify Address")
    buyers_order_no: Optional[str] = Field(default=None, alias='Buyers_Order_No', description="The buyer's purchase order number. Also printed as: Buyers Order Number")
    buyers_order_date: Optional[str] = Field(default=None, alias='Buyers_Order_Date', description="Date of that buyer's purchase order. Also printed as: Buyers Order Date")
    other_ref_no: Optional[str] = Field(default=None, alias='Other_Ref_No', description='Any other reference number recorded for this shipment. Also printed as: Other Ref Number')
    terms_of_delivery___payment: Optional[str] = Field(default=None, alias='Terms_of_Delivery_&_Payment', description='The Incoterm and payment terms agreed for this shipment (e.g. FOB/CIF, Letter of Credit, Advance). Also printed as: Terms Of Delivery & Payment')


class ExportExcGeneralChunk2Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    origin_country_code: Optional[str] = Field(default=None, alias='Origin_Country_Code', description='Country code of origin of the goods. Also printed as: Origin Country Code')
    type_of_shipment: Optional[str] = Field(default=None, alias='Type_of_Shipment', description='Category of shipment (e.g. Outright Sale, Sample, Gift, Export on Lease, Replacement). Also printed as: Type Of Shipment')
    specify_if_other_type_of_shipment: Optional[str] = Field(default=None, alias='Specify_if_Other_Type_of_Shipment', description='Free-text description used when Type_of_Shipment is "Other". Also printed as: Specify If Other Type Of Shipment')
    permission_no: Optional[str] = Field(default=None, alias='Permission_No', description='Permission/license number authorizing this export, if applicable. Also printed as: Permission Number')
    permission_date: Optional[str] = Field(default=None, alias='Permission_Date', description='Date of that permission. Also printed as: Permission Date')
    export_under: Optional[str] = Field(default=None, alias='Export_Under', description='The scheme/claim under which the goods are exported (e.g. Drawback, DEEC, EPCG, Free Shipping Bill). Also printed as: Export Under')
    discharge_port_code: Optional[str] = Field(default=None, alias='Discharge_Port_Code', description='Port code where the goods will be discharged/unloaded. Also printed as: Discharge Port Code')
    discharge_country_code: Optional[str] = Field(default=None, alias='Discharge_Country_Code', description='Country code of the discharge port. Also printed as: Discharge Country Code')
    destination_port_code: Optional[str] = Field(default=None, alias='Destination_Port_Code', description='Final destination port code. Also printed as: Destination Port Code')
    destination_country_code: Optional[str] = Field(default=None, alias='Destination_Country_Code', description='Final destination country code. Also printed as: Destination Country Code')
    nature_of_cargo: Optional[str] = Field(default=None, alias='Nature_of_Cargo', description='Category of cargo (e.g. Containerized, Breakbulk, Bulk, LCL). Also printed as: Nature Of Cargo')
    total_packages: Optional[str] = Field(default=None, alias='Total_Packages', description='Total number of packages in the shipment. Also printed as: Total Packages')


class ExportExcGeneralChunk3Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    package_unit_code: Optional[str] = Field(default=None, alias='Package_Unit_Code', description='Unit code for the package count (e.g. Cartons, Boxes, Pallets). Also printed as: Package Unit Code')
    loose_packets: Optional[str] = Field(default=None, alias='Loose_Packets', description='Count of any loose (unpacked) packets in the shipment. Also printed as: Loose Packets')
    gross_weight: Optional[str] = Field(default=None, alias='Gross_Weight', description='Total gross weight of the shipment. Also printed as: Gross Weight')
    net_weight: Optional[str] = Field(default=None, alias='Net_Weight', description='Total net weight of the shipment. Also printed as: Net Weight')
    weight_unit_code: Optional[str] = Field(default=None, alias='Weight_Unit_Code', description='Unit of weight used (e.g. KGS, TON). Also printed as: Weight Unit Code')
    origin_state_code: Optional[str] = Field(default=None, alias='Origin_State_Code', description='State code within India where the goods originated. Also printed as: Origin State Code')
    marks___no: Optional[str] = Field(default=None, alias='Marks_&_No.', description='Shipping marks and numbers printed on the packages. Also printed as: Marks & No.')
    warehouse_code: Optional[str] = Field(default=None, alias='Warehouse_Code', description='Bonded warehouse code, if the goods are exported ex-bond. Also printed as: Warehouse Code')


class ExportExcSezInfoChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    mode_of_transport: Optional[str] = Field(default=None, alias='Mode_of_Transport', description='Mode of transport for this shipment. Also printed as: Mode Of Transport')
    purpose_of_transaction: Optional[str] = Field(default=None, alias='Purpose_of_Transaction', description='The commercial purpose of this export transaction. Also printed as: Purpose Of Transaction')
    mode_of_realisation: Optional[str] = Field(default=None, alias='Mode_of_Realisation', description='How export proceeds will be realized (e.g. Advance payment, Letter of Credit, DA/DP). Also printed as: Mode Of Realisation')
    bank_for_receiving_payment: Optional[str] = Field(default=None, alias='Bank_for_Receiving_Payment', description='Name/code of the bank that will receive the export payment. Also printed as: Bank For Receiving Payment')
    is_acu_payment: Optional[str] = Field(default=None, alias='Is_ACU_Payment', description='Y/N flag -- whether payment is through the Asian Clearing Union mechanism. Also printed as: Is Acu Payment')
    declaration_date: Optional[str] = Field(default=None, alias='Declaration_Date', description="Date of this shipping bill's declaration. Also printed as: Declaration Date")
    is_provisional_assessment: Optional[str] = Field(default=None, alias='Is_Provisional_Assessment', description='Y/N flag -- whether this shipping bill is provisionally assessed. Also printed as: Is Provisional Assessment')
    is_joint_filing_declaration: Optional[str] = Field(default=None, alias='Is_Joint_Filing_Declaration', description='Y/N flag -- whether this is a joint-filing declaration (CHA + exporter). Also printed as: Is Joint Filing Declaration')
    is_export_under_sezact2005_rule2006: Optional[str] = Field(default=None, alias='Is_Export_under_SEZAct2005_Rule2006', description='Y/N flag -- whether the export is under the SEZ Act 2005 / SEZ Rules 2006. Also printed as: Is Export Under Sezact2005 Rule2006')
    will_goods_move_out_in_parts: Optional[str] = Field(default=None, alias='Will_Goods_move_out_in_parts', description='Y/N flag -- whether the shipment will physically move out in multiple partial consignments. Also printed as: Will Goods Move Out In Parts')
    is_free_export_under_ftp_para2_01: Optional[str] = Field(default=None, alias='Is_Free_Export_under_FTP_Para2.01', description='Y/N flag -- whether claimed as a "free export" under Foreign Trade Policy paragraph 2.01. Also printed as: Is Free Export Under Ftp Para2.01')
    is_reward_claimed_under_meis: Optional[str] = Field(default=None, alias='Is_Reward_claimed_under_MEIS', description='Y/N flag -- whether a reward is claimed under MEIS (Merchandise Exports from India Scheme). Also printed as: Is Reward Claimed Under Meis')


class ExportExcSezInfoChunk1Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    is_ecommerce_transaction: Optional[str] = Field(default=None, alias='Is_eCommerce_Transaction', description='Y/N flag -- whether this export was sold via an e-commerce platform. Also printed as: Is Ecommerce Transaction')
    ecommerce_webportal: Optional[str] = Field(default=None, alias='eCommerce_webportal', description='Name of the e-commerce platform used, if applicable. Also printed as: Ecommerce Webportal')
    courier_detail: Optional[str] = Field(default=None, alias='Courier_detail', description='Courier company details, for courier-mode exports. Also printed as: Courier Detail')
    firc_ref_id: Optional[str] = Field(default=None, alias='FIRC_Ref_ID', description='Foreign Inward Remittance Certificate reference ID. Also printed as: Firc Ref Id')
    awb_no: Optional[str] = Field(default=None, alias='AWB_No.', description='Air Waybill number (for air shipments). Also printed as: Awb No.')
    is_client_detail_applicable: Optional[str] = Field(default=None, alias='Is_Client_detail_applicable', description='Y/N flag -- whether a separate client (end buyer) detail block applies. Also printed as: Is Client Detail Applicable')
    client_type: Optional[str] = Field(default=None, alias='Client_Type', description='Category of the client party. Also printed as: Client Type')
    client_iec: Optional[str] = Field(default=None, alias='Client_IEC', description="The client's Importer-Exporter Code. Also printed as: Client Iec")
    client_gstin: Optional[str] = Field(default=None, alias='Client_GSTIN', description="The client's GST registration number. Also printed as: Client Gstin")
    client_name: Optional[str] = Field(default=None, alias='Client_Name', description="The client's name. Also printed as: Client Name")
    client_address: Optional[str] = Field(default=None, alias='Client_Address', description="The client's address. Also printed as: Client Address")
    client_country_code: Optional[str] = Field(default=None, alias='Client_Country_Code', description="The client's country code. Also printed as: Client Country Code")


class ExportExcSezInfoChunk2Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    is_third_party_detail_applicable: Optional[str] = Field(default=None, alias='Is_Third_Party_detail_applicable', description='Y/N flag -- whether a third-party exporter detail block applies (third-party exports). Also printed as: Is Third Party Detail Applicable')
    third_party_iec: Optional[str] = Field(default=None, alias='Third_Party_IEC', description="The third party's Importer-Exporter Code. Also printed as: Third Party Iec")
    third_party_branchsrno: Optional[str] = Field(default=None, alias='Third_Party_BranchSrNo', description="The third party's branch serial number. Also printed as: Third Party Branchsrno")
    third_party_name: Optional[str] = Field(default=None, alias='Third_Party_Name', description='Name of the third party associated with this invoice/shipment. Also printed as: Third Party Name')
    third_party_address: Optional[str] = Field(default=None, alias='Third_Party_Address', description="The third party's address. Also printed as: Third Party Address")
    third_party_city: Optional[str] = Field(default=None, alias='Third_Party_City', description="The third party's city. Also printed as: Third Party City")
    third_party_pin: Optional[str] = Field(default=None, alias='Third_Party_PIN', description="The third party's postal/PIN code. Also printed as: Third Party Pin")
    third_party_country_code: Optional[str] = Field(default=None, alias='Third_Party_Country_Code', description="The third party's country code. Also printed as: Third Party Country Code")
    invoice_value: Optional[float | str] = Field(default=None, alias='Invoice_Value', description='Total invoice value declared for this shipment. Also printed as: Invoice Value')
    invoice_value_currency_code: Optional[float | str] = Field(default=None, alias='Invoice_Value_Currency_Code', description='Currency code of that invoice value. Also printed as: Invoice Value Currency Code')


class ExportExcPackingDetailChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_packet: Optional[str] = Field(default=None, alias='From_Packet', description='Starting packet/package number in this packing range. Also printed as: From Packet')
    to_packet: Optional[str] = Field(default=None, alias='To_Packet', description='Ending packet/package number in this packing range. Also printed as: To Packet')
    packet_unit_code: Optional[str] = Field(default=None, alias='Packet_Unit_Code', description='Unit code used for the packet count. Also printed as: Packet Unit Code')


class ExportExcStuffingInfoChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    goods_stuffed_at: Optional[str] = Field(default=None, alias='Goods_Stuffed_at', description='Location where the goods were stuffed into the container (e.g. Factory, CFS). Also printed as: Goods Stuffed At')
    is_sample_accompanied: Optional[str] = Field(default=None, alias='Is_Sample_Accompanied', description='Y/N flag -- whether a sample accompanies the shipment for examination. Also printed as: Is Sample Accompanied')
    cfs_location: Optional[str] = Field(default=None, alias='CFS_Location', description='Container Freight Station location used. Also printed as: Cfs Location')
    factory_address: Optional[str] = Field(default=None, alias='Factory_Address', description='Address of the factory where stuffing occurred (for factory stuffing). Also printed as: Factory Address')
    seal_type: Optional[str] = Field(default=None, alias='Seal_Type', description='Type of seal used on the container (e.g. One-Time Lock, RFID). Also printed as: Seal Type')
    seal_no: Optional[str] = Field(default=None, alias='Seal_No', description='Seal number affixed after examination (ANNEXURE_C1) or to the container (STUFFING_INFO). Also printed as: Seal Number')
    agency_name: Optional[str] = Field(default=None, alias='Agency_Name', description='Name of the agency that supervised/sealed the stuffing. Also printed as: Agency Name')


class ExportExcAnnexureC1Chunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    iec_of_eou: Optional[str] = Field(default=None, alias='IEC_of_EOU', description='Importer-Exporter Code of the EOU (Export Oriented Unit), if applicable. Also printed as: Iec Of Eou')
    branchsrno: Optional[str] = Field(default=None, alias='BranchSrNo', description='Branch serial number of the exporter. Also printed as: Branchsrno')
    examination_date: Optional[str] = Field(default=None, alias='Examination_Date', description='Date the goods were examined. Also printed as: Examination Date')
    examining_officer: Optional[str] = Field(default=None, alias='Examining_Officer', description='Name of the customs officer who examined the goods. Also printed as: Examining Officer')
    examining_officer_designation: Optional[str] = Field(default=None, alias='Examining_Officer_Designation', description='Designation/rank of that examining officer. Also printed as: Examining Officer Designation')
    supervising_officer: Optional[str] = Field(default=None, alias='Supervising_Officer', description='Name of the supervising officer. Also printed as: Supervising Officer')
    supervising_officer_designation: Optional[str] = Field(default=None, alias='Supervising_Officer_Designation', description='Designation/rank of that supervising officer. Also printed as: Supervising Officer Designation')
    commissionerate: Optional[float | str] = Field(default=None, alias='Commissionerate', description='The customs Commissionerate having jurisdiction. Also printed as: Commissionerate')
    division: Optional[str] = Field(default=None, alias='Division', description='The customs Division within that Commissionerate. Also printed as: Division')
    range_: Optional[str] = Field(default=None, alias='Range', description='The customs Range within that Division. Also printed as: Range')
    verified_by_examining_officer: Optional[str] = Field(default=None, alias='Verified_by_Examining_Officer', description='Y/N flag -- whether verification by the examining officer is confirmed. Also printed as: Verified By Examining Officer')
    is_sample_forwarded: Optional[str] = Field(default=None, alias='Is_Sample_Forwarded', description='Y/N flag -- whether a sample was forwarded for testing. Also printed as: Is Sample Forwarded')


class ExportExcAnnexureC1Chunk1Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    seal_no: Optional[str] = Field(default=None, alias='Seal_No', description='Seal number affixed after examination (ANNEXURE_C1) or to the container (STUFFING_INFO). Also printed as: Seal Number')


class ExportExcExbondInfoChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    be_no: Optional[str] = Field(default=None, alias='BE_No', description='The Bill of Entry number under which the goods were originally warehoused (for ex-bond export). Also printed as: Be Number')
    be_date: Optional[str] = Field(default=None, alias='BE_Date', description='Date of that Bill of Entry. Also printed as: Be Date')
    vessel: Optional[str] = Field(default=None, alias='Vessel', description='Name of the vessel on which the goods originally arrived. Also printed as: Vessel')
    voyage: Optional[str] = Field(default=None, alias='Voyage', description='Voyage number of that vessel. Also printed as: Voyage')
    igm_no: Optional[str] = Field(default=None, alias='IGM_No.', description='Import General Manifest number for that original arrival. Also printed as: Igm No.')
    igm_date: Optional[str] = Field(default=None, alias='IGM_Date', description='Date of that Import General Manifest. Also printed as: Igm Date')
    no_of_pkg: Optional[float | str] = Field(default=None, alias='No_of_Pkg', description='Number of packages being cleared ex-bond. Also printed as: Number Of Pkg')
    bond_no: Optional[str] = Field(default=None, alias='Bond_No.', description='Warehousing bond number. Also printed as: Bond No.')
    bond_date: Optional[str] = Field(default=None, alias='Bond_Date', description='Date of that warehousing bond. Also printed as: Bond Date')
    warehouse: Optional[str] = Field(default=None, alias='Warehouse', description='Name/code of the bonded warehouse. Also printed as: Warehouse')


class ExportExcContainerChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    container_no: Optional[str] = Field(default=None, alias='Container_No.', description='Container number. Also printed as: Container No.')
    container_type: Optional[str] = Field(default=None, alias='Container_Type', description='Type/size of container (e.g. 20ft, 40ft, Reefer). Also printed as: Container Type')
    packages_stuffed: Optional[str] = Field(default=None, alias='Packages_Stuffed', description='Number of packages stuffed into this container. Also printed as: Packages Stuffed')
    gross_weight: Optional[str] = Field(default=None, alias='Gross_Weight', description='Total gross weight of the shipment. Also printed as: Gross Weight')
    gross_weight_unit_code: Optional[str] = Field(default=None, alias='Gross_Weight_Unit_Code', description="Unit code for the container's gross weight. Also printed as: Gross Weight Unit Code")
    seal_no: Optional[str] = Field(default=None, alias='Seal_No.', description='Seal number on this container. Also printed as: Seal No.')
    seal_date: Optional[str] = Field(default=None, alias='Seal_Date', description='Date the seal was affixed. Also printed as: Seal Date')
    seal_type: Optional[str] = Field(default=None, alias='Seal_Type', description='Type of seal used on the container (e.g. One-Time Lock, RFID). Also printed as: Seal Type')
    seal_device_id: Optional[str] = Field(default=None, alias='Seal_Device_ID', description='Electronic seal device identifier, if an e-seal is used. Also printed as: Seal Device Id')
    movement_document_type: Optional[str] = Field(default=None, alias='Movement_Document_Type', description="Type of document authorizing this container's movement (e.g. gate pass, transport permit). Also printed as: Movement Document Type")
    movement_document_no: Optional[str] = Field(default=None, alias='Movement_Document_No.', description='Number of that movement document. Also printed as: Movement Document No.')


class ExportExcInvoicesChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    invsrno: Optional[str] = Field(default=None, alias='InvSrNo', description='Serial number of this invoice within the shipping bill (a shipping bill can cover multiple invoices). Also printed as: Invsrno')
    inv_no: Optional[str] = Field(default=None, alias='Inv_No', description='The commercial invoice number. Also printed as: Inv Number')
    date: Optional[str] = Field(default=None, alias='Date', description="The commercial invoice's own date. Also printed as: Date")
    toi: Optional[str] = Field(default=None, alias='TOI', description='UNCONFIRMED MEANING -- likely "Terms of Invoice" (e.g. an Incoterm such as FOB/CIF/EXW) or possibly "Type of Invoice"; verify against a real filed BOE before trusting this. Also printed as: Toi')
    toi_place: Optional[str] = Field(default=None, alias='TOI_Place', description='The named place associated with this invoice\'s Incoterm (e.g. "FOB Mumbai"). Also printed as: Toi Place')
    inv_currency_code: Optional[str] = Field(default=None, alias='Inv_Currency_Code', description='Currency code this invoice is denominated in. Also printed as: Inv Currency Code')
    price_includes_n_f_i_b: Optional[float | str] = Field(default=None, alias='Price_Includes_N_F_I_B', description='UNCONFIRMED MEANING -- appears to flag whether the price includes Normal/Freight/Insurance/[B] components; verify the exact breakdown against a real filed shipping bill. Also printed as: Price Includes N F I B')
    taxable_value_for_igst: Optional[float | str] = Field(default=None, alias='Taxable_value_for_IGST', description='The value used as the basis for IGST calculation. Also printed as: Taxable Value For Igst')
    invoice_value: Optional[float | str] = Field(default=None, alias='Invoice_Value', description='Total invoice value declared for this shipment. Also printed as: Invoice Value')
    product_value: Optional[float | str] = Field(default=None, alias='Product_Value', description='Value of the goods alone, excluding freight/insurance/other charges. Also printed as: Product Value')
    packing_fob_amount: Optional[float | str] = Field(default=None, alias='Packing_FOB_Amount', description='FOB value of the packing charges. Also printed as: Packing Fob Amount')
    freight_currency_code: Optional[str] = Field(default=None, alias='Freight_Currency_Code', description='Currency of the freight charge. Also printed as: Freight Currency Code')


class ExportExcInvoicesChunk1Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    freight_percentage: Optional[float | str] = Field(default=None, alias='Freight_Percentage', description='Freight expressed as a percentage of value, if applicable. Also printed as: Freight Percentage')
    freight_amount: Optional[float | str] = Field(default=None, alias='Freight_Amount', description='Freight charge amount. Also printed as: Freight Amount')
    insurance_currency_code: Optional[str] = Field(default=None, alias='Insurance_Currency_Code', description='Currency of the insurance charge. Also printed as: Insurance Currency Code')
    insurance_percentage: Optional[float | str] = Field(default=None, alias='Insurance_Percentage', description='Insurance expressed as a percentage of value, if applicable. Also printed as: Insurance Percentage')
    insurance_amount: Optional[float | str] = Field(default=None, alias='Insurance_Amount', description='Insurance charge amount. Also printed as: Insurance Amount')
    discount_currency: Optional[str] = Field(default=None, alias='Discount_Currency', description='Currency of any discount applied. Also printed as: Discount Currency')
    discount_percentage: Optional[float | str] = Field(default=None, alias='Discount_Percentage', description='Discount as a percentage. Also printed as: Discount Percentage')
    discount_amount: Optional[float | str] = Field(default=None, alias='Discount_Amount', description='Discount amount. Also printed as: Discount Amount')
    other_deduction_currency: Optional[str] = Field(default=None, alias='Other_Deduction_Currency', description='Currency of any other deduction. Also printed as: Other Deduction Currency')
    other_deduction_percentage: Optional[float | str] = Field(default=None, alias='Other_Deduction_Percentage', description='Other deduction as a percentage. Also printed as: Other Deduction Percentage')
    other_deduction_amount: Optional[float | str] = Field(default=None, alias='Other_Deduction_Amount', description='Other deduction amount. Also printed as: Other Deduction Amount')
    commission_currency: Optional[str] = Field(default=None, alias='Commission_Currency', description='Currency of any commission. Also printed as: Commission Currency')


class ExportExcInvoicesChunk2Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    commission_percentage: Optional[float | str] = Field(default=None, alias='Commission_Percentage', description='Commission as a percentage. Also printed as: Commission Percentage')
    commission_amount: Optional[float | str] = Field(default=None, alias='Commission_Amount', description='Commission amount. Also printed as: Commission Amount')
    third_party_name: Optional[str] = Field(default=None, alias='Third_Party_Name', description='Name of the third party associated with this invoice/shipment. Also printed as: Third Party Name')
    third_party_address: Optional[str] = Field(default=None, alias='Third_Party_Address', description="The third party's address. Also printed as: Third Party Address")
    third_party_city: Optional[str] = Field(default=None, alias='Third_Party_City', description="The third party's city. Also printed as: Third Party City")
    third_party_pin: Optional[str] = Field(default=None, alias='Third_Party_PIN', description="The third party's postal/PIN code. Also printed as: Third Party Pin")
    third_party_state: Optional[str] = Field(default=None, alias='Third_Party_State', description="The third party's state. Also printed as: Third Party State")
    third_party_country_code: Optional[str] = Field(default=None, alias='Third_Party_Country_Code', description="The third party's country code. Also printed as: Third Party Country Code")
    buyer_name: Optional[str] = Field(default=None, alias='Buyer_Name', description="The buyer's name for this invoice. Also printed as: Buyer Name")
    buyer_address: Optional[str] = Field(default=None, alias='Buyer_Address', description="The buyer's address. Also printed as: Buyer Address")
    buyer_city: Optional[str] = Field(default=None, alias='Buyer_City', description="The buyer's city. Also printed as: Buyer City")
    buyer_pin: Optional[str] = Field(default=None, alias='Buyer_PIN', description="The buyer's postal/PIN code. Also printed as: Buyer Pin")


class ExportExcInvoicesChunk3Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    buyer_state: Optional[str] = Field(default=None, alias='Buyer_State', description="The buyer's state. Also printed as: Buyer State")
    buyer_country_code: Optional[str] = Field(default=None, alias='Buyer_Country_Code', description="The buyer's country code. Also printed as: Buyer Country Code")
    export_contract_no: Optional[str] = Field(default=None, alias='Export_Contract_No.', description='The export sales contract number. Also printed as: Export Contract No.')
    export_contract_date: Optional[str] = Field(default=None, alias='Export_Contract_Date', description='Date of that export contract. Also printed as: Export Contract Date')
    nature_of_payment: Optional[str] = Field(default=None, alias='Nature_of_Payment', description='Category of payment arrangement (e.g. Advance, Open Account, Letter of Credit). Also printed as: Nature Of Payment')
    payment_period: Optional[str] = Field(default=None, alias='Payment_Period', description='Credit period allowed for payment (e.g. number of days). Also printed as: Payment Period')
    aeo_code: Optional[str] = Field(default=None, alias='AEO_Code', description='Authorized Economic Operator code, if the exporter/invoice qualifies for AEO benefits. Also printed as: Aeo Code')
    aeo_country_code: Optional[str] = Field(default=None, alias='AEO_Country_Code', description='Country code of the AEO scheme being claimed under. Also printed as: Aeo Country Code')
    aeo_role: Optional[str] = Field(default=None, alias='AEO_Role', description='The AEO role/category applicable. Also printed as: Aeo Role')


class ExportExcItemsChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    invsrno: Optional[str] = Field(default=None, alias='InvSrNo', description='Serial number of this invoice within the shipping bill (a shipping bill can cover multiple invoices). Also printed as: Invsrno')
    itemsrno: Optional[str] = Field(default=None, alias='ItemSrNo', description='Serial number of this item within its invoice. Also printed as: Itemsrno')
    product_desc: Optional[str] = Field(default=None, alias='Product_Desc', description='Commercial description of the goods for this line item. Also printed as: Product Description')
    ritc: Optional[str] = Field(default=None, alias='RITC', description='The RITC (Revised Indian Trade Classification) / HSN code for this item. Also printed as: Ritc')
    quantity: Optional[str] = Field(default=None, alias='Quantity', description='The quantity of this line item. Also printed as: Quantity')
    quantity_unit_code: Optional[str] = Field(default=None, alias='Quantity_Unit_Code', description="Unit code for this item's Quantity. Also printed as: Quantity Unit Code")
    unit_price: Optional[float | str] = Field(default=None, alias='Unit_Price', description='Price per unit. Also printed as: Unit Price')
    per_unit_no: Optional[str] = Field(default=None, alias='Per_Unit_No.', description='The "per" basis the unit price is quoted against (e.g. price per 100 units). Also printed as: Percentage Unit No.')
    sqc_quantity: Optional[str] = Field(default=None, alias='SQC_Quantity', description='Statistical Quantity -- the quantity expressed in the standard unit used for trade statistics. Also printed as: Sqc Quantity')
    sqc_quantity_unit_code: Optional[str] = Field(default=None, alias='SQC_Quantity_Unit_Code', description='Unit code for that statistical quantity. Also printed as: Sqc Quantity Unit Code')
    exim_scheme_code: Optional[str] = Field(default=None, alias='Exim_Scheme_Code', description='The EXIM (export-import policy) scheme code this item is exported under. Also printed as: Exim Scheme Code')
    nfei_category_code: Optional[str] = Field(default=None, alias='NFEI_Category_Code', description='Net Foreign Exchange Earnings category code (relevant to EOU/SEZ incentive calculations). Also printed as: Nfei Category Code')


class ExportExcItemsChunk1Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    pmv: Optional[float | str] = Field(default=None, alias='PMV', description='Present Market Value of the item. Also printed as: Pmv')
    pmv_currency_code: Optional[float | str] = Field(default=None, alias='PMV_Currency_Code', description='Currency of that present market value. Also printed as: Pmv Currency Code')
    igst_pay_status: Optional[str] = Field(default=None, alias='IGST_Pay_Status', description='Whether IGST has been paid, is exempted, or is under LUT/bond for this item. Also printed as: Igst Pay Status')
    igst_taxable_amt: Optional[float | str] = Field(default=None, alias='IGST_Taxable_Amt', description='Taxable amount for IGST purposes. Also printed as: Igst Taxable Amount')
    igst_rate: Optional[float | str] = Field(default=None, alias='IGST_Rate', description='IGST rate applicable. Also printed as: Igst Rate')
    igst_amount: Optional[float | str] = Field(default=None, alias='IGST_Amount', description='IGST amount. Also printed as: Igst Amount')
    compcessrate: Optional[float | str] = Field(default=None, alias='CompCessRate', description='GST Compensation Cess rate applicable. Also printed as: Compcessrate')
    is_reward_item: Optional[str] = Field(default=None, alias='Is_Reward_Item', description='Y/N flag -- whether this item is claimed under a reward/incentive scheme (e.g. RoDTEP, MEIS). Also printed as: Is Reward Item')
    end_use_code: Optional[str] = Field(default=None, alias='End_Use_Code', description='Declared end-use code for the goods. Also printed as: End Use Code')
    origin_district_code: Optional[str] = Field(default=None, alias='Origin_District_Code', description='District code (within India) where the item originated. Also printed as: Origin District Code')
    ptafta_code: Optional[str] = Field(default=None, alias='PTAFTA_Code', description='Preferential Trade Agreement / Free Trade Agreement code claimed for this item. Also printed as: Ptafta Code')
    accessories_status: Optional[str] = Field(default=None, alias='Accessories_Status', description='Whether accessories are included with this item. Also printed as: Accessories Status')


class ExportExcItemsChunk2Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    accessory_info: Optional[str] = Field(default=None, alias='Accessory_Info', description='Details of those accessories. Also printed as: Accessory Info')
    dbk_sr__no: Optional[str] = Field(default=None, alias='DBK_Sr._No', description='Drawback schedule serial number claimed for this item. Also printed as: Dbk Sr. Number')
    dbk_quantity: Optional[str] = Field(default=None, alias='DBK_Quantity', description='Quantity against which drawback is claimed. Also printed as: Dbk Quantity')
    dbk_under: Optional[str] = Field(default=None, alias='DBK_Under', description='The drawback scheme/section claimed under. Also printed as: Dbk Under')
    product_material_code: Optional[str] = Field(default=None, alias='Product_Material_Code', description='Internal/customs material code for the product. Also printed as: Product Material Code')
    tp_exporter_name: Optional[str] = Field(default=None, alias='TP_Exporter_Name', description="Third-party exporter's name for this item (third-party export transactions). Also printed as: Tp Exporter Name")
    tp_exporter_ie_code: Optional[str] = Field(default=None, alias='TP_Exporter_IE_Code', description="Third-party exporter's Importer-Exporter Code. Also printed as: Tp Exporter Ie Code")
    tp_exporter_brs_no: Optional[str] = Field(default=None, alias='TP_Exporter_BrS_No.', description="Third-party exporter's branch serial number. Also printed as: Tp Exporter Brs No.")
    tp_exporter_reg_type: Optional[str] = Field(default=None, alias='TP_Exporter_Reg_Type', description='Type of registration held by the third-party exporter. Also printed as: Tp Exporter Reg Type')
    tp_exporter_reg_no: Optional[str] = Field(default=None, alias='TP_Exporter_Reg_No', description='Registration number of the third-party exporter. Also printed as: Tp Exporter Reg Number')
    tp_exporter_addr: Optional[str] = Field(default=None, alias='TP_Exporter_Addr', description="Third-party exporter's address. Also printed as: Tp Exporter Addr")
    manufacturer: Optional[str] = Field(default=None, alias='Manufacturer', description='Name of the actual manufacturer of the goods. Also printed as: Manufacturer')


class ExportExcItemsChunk3Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    manufacturer_addr: Optional[str] = Field(default=None, alias='Manufacturer_Addr', description="The manufacturer's address. Also printed as: Manufacturer Addr")
    manufacturer_codetype: Optional[str] = Field(default=None, alias='Manufacturer_CodeType', description='The type/scheme of the manufacturer code given below. Also printed as: Manufacturer Codetype')
    manufacturer_code: Optional[str] = Field(default=None, alias='Manufacturer_Code', description="The manufacturer's registration code. Also printed as: Manufacturer Code")
    manufacturer_country: Optional[str] = Field(default=None, alias='Manufacturer_Country', description="The manufacturer's country. Also printed as: Manufacturer Country")
    manufacturer_state: Optional[str] = Field(default=None, alias='Manufacturer_State', description="The manufacturer's state/province. Also printed as: Manufacturer State")
    manuf_postal_code: Optional[str] = Field(default=None, alias='Manuf_Postal_Code', description="The manufacturer's postal/ZIP code. Also printed as: Manuf Postal Code")
    sourcestate: Optional[str] = Field(default=None, alias='SourceState', description='The Indian state the goods were sourced from. Also printed as: Sourcestate')
    transitcountry: Optional[str] = Field(default=None, alias='TransitCountry', description='Country through which the goods transited. Also printed as: Transitcountry')
    cenvat_certificate_no: Optional[str] = Field(default=None, alias='CENVAT_Certificate_No.', description='Number of a CENVAT (legacy excise input credit) certificate associated with this item. Also printed as: Cenvat Certificate No.')
    cenvat_certificate_date: Optional[str] = Field(default=None, alias='CENVAT_Certificate_Date', description='Date that CENVAT certificate was issued. Also printed as: Cenvat Certificate Date')
    cenvat_certificate_valid_upto: Optional[float | str] = Field(default=None, alias='CENVAT_Certificate_Valid_upto', description='Validity expiry date of that CENVAT certificate. Also printed as: Cenvat Certificate Valid Upto')
    cenvat_central_excise_office_code: Optional[str] = Field(default=None, alias='CENVAT_Central_Excise_Office_Code', description='Central Excise office code that issued that CENVAT certificate. Also printed as: Cenvat Central Excise Office Code')


class ExportExcItemsChunk4Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    cenvat_assesse_code: Optional[str] = Field(default=None, alias='CENVAT_Assesse_Code', description='The assessee code registered on that CENVAT certificate. Also printed as: Cenvat Assesse Code')
    rodtep_status: Optional[str] = Field(default=None, alias='RoDTEP_Status', description='Whether RoDTEP (Remission of Duties and Taxes on Exported Products) benefit is claimed for this item. Also printed as: Rodtep Status')
    rodtep_quantity: Optional[str] = Field(default=None, alias='RoDTEP_Quantity', description='Quantity against which RoDTEP is claimed. Also printed as: Rodtep Quantity')
    rodtep_quantity_unit_code: Optional[str] = Field(default=None, alias='RoDTEP_Quantity_Unit_Code', description='Unit code for that RoDTEP quantity. Also printed as: Rodtep Quantity Unit Code')
    medicinal_plant: Optional[str] = Field(default=None, alias='Medicinal_Plant', description='Whether the item is a medicinal plant product (subject to special export regulation). Also printed as: Medicinal Plant')
    formulation: Optional[str] = Field(default=None, alias='Formulation', description='Details of the formulation, for pharmaceutical/chemical items. Also printed as: Formulation')
    surface_material_in_contact: Optional[str] = Field(default=None, alias='Surface_Material_in_Contact', description="The material in contact with the product's surface (relevant for food/pharma packaging declarations). Also printed as: Surface Material In Contact")
    gst_allowed: Optional[str] = Field(default=None, alias='GST_Allowed', description='Y/N flag -- whether GST input credit is allowed for this item. Also printed as: Gst Allowed')
    lgd_code: Optional[str] = Field(default=None, alias='LGD_Code', description='UNCONFIRMED MEANING -- possibly a "Legal Goods Description" or scheme-specific code; verify against a real filed shipping bill before trusting this. Also printed as: Lgd Code')


class ExportExcDeecChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    invsrno: Optional[str] = Field(default=None, alias='InvSrNo', description='Serial number of this invoice within the shipping bill (a shipping bill can cover multiple invoices). Also printed as: Invsrno')
    itemsrno: Optional[str] = Field(default=None, alias='ItemSrNo', description='Serial number of this item within its invoice. Also printed as: Itemsrno')
    license_reference_no: Optional[str] = Field(default=None, alias='License_Reference_No.', description='The license reference number for this scheme (Advance Authorization/EPCG/DFIA, per the sheet). Also printed as: License Reference No.')
    license_registration_no: Optional[str] = Field(default=None, alias='License_Registration_No', description="That license's registration number at customs. Also printed as: License Registration Number")
    license_registration_date: Optional[str] = Field(default=None, alias='License_Registration_Date', description='Date of that registration. Also printed as: License Registration Date')
    export_item_srno: Optional[str] = Field(default=None, alias='Export_Item_SrNo.', description='Serial number of this export item as listed on the license. Also printed as: Export Item Srno.')
    export_item_qty: Optional[float | str] = Field(default=None, alias='Export_Item_Qty', description='Quantity of this export item being debited against the license. Also printed as: Export Item Quantity')
    import_item_srno: Optional[str] = Field(default=None, alias='Import_Item_SrNo.', description='Serial number of the corresponding duty-free import item allowed under this license. Also printed as: Import Item Srno.')
    import_item_type: Optional[str] = Field(default=None, alias='Import_Item_Type', description='Category/type of that duty-free import item. Also printed as: Import Item Type')
    import_item_description: Optional[str] = Field(default=None, alias='Import_Item_Description', description='Description of that duty-free import item. Also printed as: Import Item Description')
    import_item_quantity: Optional[str] = Field(default=None, alias='Import_Item_Quantity', description='Quantity of that duty-free import item. Also printed as: Import Item Quantity')
    import_item_quantity_unit_code: Optional[str] = Field(default=None, alias='Import_Item_Quantity_Unit_Code', description='Unit code for that import item quantity. Also printed as: Import Item Quantity Unit Code')


class ExportExcEpcgChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    invsrno: Optional[str] = Field(default=None, alias='InvSrNo', description='Serial number of this invoice within the shipping bill (a shipping bill can cover multiple invoices). Also printed as: Invsrno')
    itemsrno: Optional[str] = Field(default=None, alias='ItemSrNo', description='Serial number of this item within its invoice. Also printed as: Itemsrno')
    license_reference_no: Optional[str] = Field(default=None, alias='License_Reference_No.', description='The license reference number for this scheme (Advance Authorization/EPCG/DFIA, per the sheet). Also printed as: License Reference No.')
    license_registration_no: Optional[str] = Field(default=None, alias='License_Registration_No', description="That license's registration number at customs. Also printed as: License Registration Number")
    license_registration_date: Optional[str] = Field(default=None, alias='License_Registration_Date', description='Date of that registration. Also printed as: License Registration Date')
    export_item_srno: Optional[str] = Field(default=None, alias='Export_Item_SrNo.', description='Serial number of this export item as listed on the license. Also printed as: Export Item Srno.')
    export_item_qty: Optional[float | str] = Field(default=None, alias='Export_Item_Qty', description='Quantity of this export item being debited against the license. Also printed as: Export Item Quantity')
    import_item_srno: Optional[str] = Field(default=None, alias='Import_Item_SrNo.', description='Serial number of the corresponding duty-free import item allowed under this license. Also printed as: Import Item Srno.')
    import_item_type: Optional[str] = Field(default=None, alias='Import_Item_Type', description='Category/type of that duty-free import item. Also printed as: Import Item Type')
    import_item_description: Optional[str] = Field(default=None, alias='Import_Item_Description', description='Description of that duty-free import item. Also printed as: Import Item Description')
    import_item_quantity: Optional[str] = Field(default=None, alias='Import_Item_Quantity', description='Quantity of that duty-free import item. Also printed as: Import Item Quantity')
    import_item_quantity_unit_code: Optional[str] = Field(default=None, alias='Import_Item_Quantity_Unit_Code', description='Unit code for that import item quantity. Also printed as: Import Item Quantity Unit Code')


class ExportExcDfiaChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    invsrno: Optional[str] = Field(default=None, alias='InvSrNo', description='Serial number of this invoice within the shipping bill (a shipping bill can cover multiple invoices). Also printed as: Invsrno')
    itemsrno: Optional[str] = Field(default=None, alias='ItemSrNo', description='Serial number of this item within its invoice. Also printed as: Itemsrno')
    license_reference_no: Optional[str] = Field(default=None, alias='License_Reference_No.', description='The license reference number for this scheme (Advance Authorization/EPCG/DFIA, per the sheet). Also printed as: License Reference No.')
    license_registration_no: Optional[str] = Field(default=None, alias='License_Registration_No', description="That license's registration number at customs. Also printed as: License Registration Number")
    license_registration_date: Optional[str] = Field(default=None, alias='License_Registration_Date', description='Date of that registration. Also printed as: License Registration Date')
    export_item_srno: Optional[str] = Field(default=None, alias='Export_Item_SrNo.', description='Serial number of this export item as listed on the license. Also printed as: Export Item Srno.')
    export_item_qty: Optional[float | str] = Field(default=None, alias='Export_Item_Qty', description='Quantity of this export item being debited against the license. Also printed as: Export Item Quantity')
    import_item_srno: Optional[str] = Field(default=None, alias='Import_Item_SrNo.', description='Serial number of the corresponding duty-free import item allowed under this license. Also printed as: Import Item Srno.')
    import_item_type: Optional[str] = Field(default=None, alias='Import_Item_Type', description='Category/type of that duty-free import item. Also printed as: Import Item Type')
    import_item_description: Optional[str] = Field(default=None, alias='Import_Item_Description', description='Description of that duty-free import item. Also printed as: Import Item Description')
    import_item_quantity: Optional[str] = Field(default=None, alias='Import_Item_Quantity', description='Quantity of that duty-free import item. Also printed as: Import Item Quantity')
    import_item_quantity_unit_code: Optional[str] = Field(default=None, alias='Import_Item_Quantity_Unit_Code', description='Unit code for that import item quantity. Also printed as: Import Item Quantity Unit Code')


class ExportExcDfrcChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    invsrno: Optional[str] = Field(default=None, alias='InvSrNo', description='Serial number of this invoice within the shipping bill (a shipping bill can cover multiple invoices). Also printed as: Invsrno')
    itemsrno: Optional[str] = Field(default=None, alias='ItemSrNo', description='Serial number of this item within its invoice. Also printed as: Itemsrno')
    regn_no: Optional[str] = Field(default=None, alias='Regn_No', description='The DFRC registration number. Also printed as: Registration Number')
    prod_group: Optional[str] = Field(default=None, alias='Prod_Group', description='Product group classification under the DFRC/SION framework. Also printed as: Prod Group')
    sion_sr_no: Optional[str] = Field(default=None, alias='SION_Sr_No', description='Standard Input-Output Norms (SION) serial number applicable. Also printed as: Sion Sr Number')
    sion_ionorm_sno: Optional[str] = Field(default=None, alias='SION_IONorm_SNo', description='The specific input-output norm serial number within that SION entry. Also printed as: Sion Ionorm Sno')
    qty: Optional[float | str] = Field(default=None, alias='Qty', description='Quantity. Also printed as: Quantity')
    unit: Optional[str] = Field(default=None, alias='Unit', description='Unit of that quantity. Also printed as: Unit')
    item_type: Optional[str] = Field(default=None, alias='Item_Type', description='Category of the item. Also printed as: Item Type')
    item_description: Optional[str] = Field(default=None, alias='Item_Description', description='Description of the item. Also printed as: Item Description')
    technical_details: Optional[str] = Field(default=None, alias='Technical_Details', description='Technical specification details of the item. Also printed as: Technical Details')


class ExportExcCessAndDutyChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    invsrno: Optional[str] = Field(default=None, alias='InvSrNo', description='Serial number of this invoice within the shipping bill (a shipping bill can cover multiple invoices). Also printed as: Invsrno')
    itemsrno: Optional[str] = Field(default=None, alias='ItemSrNo', description='Serial number of this item within its invoice. Also printed as: Itemsrno')
    export_duty_schedule: Optional[float | str] = Field(default=None, alias='Export_Duty_Schedule', description='The export duty schedule/notification applicable to this item. Also printed as: Export Duty Schedule')
    quantity_for_export_duty: Optional[float | str] = Field(default=None, alias='Quantity_for_Export_Duty', description='Quantity on which export duty is computed. Also printed as: Quantity For Export Duty')
    cess_schedule: Optional[str] = Field(default=None, alias='Cess_Schedule', description='The cess schedule/notification applicable. Also printed as: Cess Schedule')
    tariff_value_for_cess: Optional[float | str] = Field(default=None, alias='Tariff_Value_for_Cess', description='The tariff value used as the basis for cess calculation. Also printed as: Tariff Value For Cess')
    tariiff_value_unit_code: Optional[float | str] = Field(default=None, alias='Tariiff_Value_Unit_Code', description='Unit code for that tariff value. Also printed as: Tariiff Value Unit Code')
    quantity_for_cess: Optional[str] = Field(default=None, alias='Quantity_for_Cess', description='Quantity on which cess is computed. Also printed as: Quantity For Cess')
    other_duty_cess_schedule: Optional[float | str] = Field(default=None, alias='Other_Duty/Cess_Schedule', description='A further, additional duty/cess schedule applicable to this item. Also printed as: Other Duty/cess Schedule')
    quantity_for_other_duty_cess: Optional[float | str] = Field(default=None, alias='Quantity_for_Other_Duty/Cess', description='Quantity for that additional duty/cess. Also printed as: Quantity For Other Duty/cess')
    third_cess_schedule: Optional[str] = Field(default=None, alias='Third_Cess_Schedule', description='A third cess schedule, if applicable. Also printed as: Third Cess Schedule')
    quantity_for_third_cess: Optional[str] = Field(default=None, alias='Quantity_for_Third_Cess', description='Quantity for that third cess. Also printed as: Quantity For Third Cess')


class ExportExcQuotaChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    inv_srno: Optional[str] = Field(default=None, alias='Inv_SrNo', description="Serial number of the invoice this row's data belongs to (links back to the INVOICES sheet). Also printed as: Inv Srno")
    item_srno: Optional[str] = Field(default=None, alias='Item_SrNo', description="Serial number of the item this row's data belongs to (links back to the ITEMS sheet). Also printed as: Item Srno")
    certificate_no: Optional[str] = Field(default=None, alias='Certificate_No.', description='Export quota certificate number. Also printed as: Certificate No.')
    agency_code: Optional[str] = Field(default=None, alias='Agency_Code', description='Code of the agency that issued the quota certificate. Also printed as: Agency Code')
    expiry_date: Optional[str] = Field(default=None, alias='Expiry_Date', description='Expiry date of that quota certificate. Also printed as: Expiry Date')
    quantity: Optional[str] = Field(default=None, alias='Quantity', description='The quantity of this line item. Also printed as: Quantity')
    quantity_unit_code: Optional[str] = Field(default=None, alias='Quantity_Unit_Code', description="Unit code for this item's Quantity. Also printed as: Quantity Unit Code")


class ExportExcAreDetailsChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    inv_srno: Optional[str] = Field(default=None, alias='Inv_SrNo', description="Serial number of the invoice this row's data belongs to (links back to the INVOICES sheet). Also printed as: Inv Srno")
    item_srno: Optional[str] = Field(default=None, alias='Item_SrNo', description="Serial number of the item this row's data belongs to (links back to the ITEMS sheet). Also printed as: Item Srno")
    are_no: Optional[str] = Field(default=None, alias='ARE_No.', description='The ARE-1 (Application for Removal of Excisable Goods for Export) number. Also printed as: Are No.')
    are_date: Optional[str] = Field(default=None, alias='ARE_Date', description='Date of that ARE-1. Also printed as: Are Date')
    commissionerate: Optional[float | str] = Field(default=None, alias='Commissionerate', description='The customs Commissionerate having jurisdiction. Also printed as: Commissionerate')
    division: Optional[str] = Field(default=None, alias='Division', description='The customs Division within that Commissionerate. Also printed as: Division')
    range_: Optional[str] = Field(default=None, alias='Range', description='The customs Range within that Division. Also printed as: Range')
    remarks: Optional[str] = Field(default=None, alias='Remarks', description='Any remarks noted on the ARE-1. Also printed as: Remarks')


class ExportExcJobWorkChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    inv_srno: Optional[str] = Field(default=None, alias='Inv_SrNo.', description="Serial number of the invoice this row's data belongs to (links back to the INVOICES sheet). Also printed as: Inv Srno.")
    item_srno: Optional[str] = Field(default=None, alias='Item_SrNo', description="Serial number of the item this row's data belongs to (links back to the ITEMS sheet). Also printed as: Item Srno")
    notification_no: Optional[str] = Field(default=None, alias='Notification_No.', description='The job-work notification number under which this export is made. Also printed as: Notification No.')
    be_no: Optional[str] = Field(default=None, alias='BE_No.', description='Bill of Entry number referenced by this row. Also printed as: Be No.')
    be_date: Optional[str] = Field(default=None, alias='BE_Date', description='Date of that Bill of Entry. Also printed as: Be Date')
    be_inv_srno: Optional[str] = Field(default=None, alias='BE_Inv_SrNo.', description="The referenced Bill of Entry's own invoice serial number. Also printed as: Be Inv Srno.")
    be_actual_inv_no: Optional[str] = Field(default=None, alias='BE_Actual_Inv_No.', description="The referenced Bill of Entry's own actual invoice number. Also printed as: Be Actual Inv No.")
    be_item_srno: Optional[str] = Field(default=None, alias='BE_Item_SrNo', description="The referenced Bill of Entry's own item serial number. Also printed as: Be Item Srno")
    imported_at: Optional[str] = Field(default=None, alias='Imported_at', description='The port/location where the original goods were imported. Also printed as: Imported At')
    be_quantity: Optional[str] = Field(default=None, alias='BE_Quantity', description='Quantity from that Bill of Entry being exported after job work. Also printed as: Be Quantity')
    be_quantity_unit_code: Optional[str] = Field(default=None, alias='BE_Quantity_Unit_Code', description='Unit code for that Bill of Entry quantity. Also printed as: Be Quantity Unit Code')


class ExportExcReExportChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    invsrno: Optional[str] = Field(default=None, alias='InvSrNo', description='Serial number of this invoice within the shipping bill (a shipping bill can cover multiple invoices). Also printed as: Invsrno')
    itemsrno: Optional[str] = Field(default=None, alias='ItemSrNo', description='Serial number of this item within its invoice. Also printed as: Itemsrno')
    be_no: Optional[str] = Field(default=None, alias='BE_No', description='Bill of Entry number referenced by this row (see the specific sheet for what it represents). Also printed as: Be Number')
    be_date: Optional[str] = Field(default=None, alias='BE_Date', description='Date of that Bill of Entry. Also printed as: Be Date')
    be_inv_sno: Optional[str] = Field(default=None, alias='BE_Inv_SNo', description="The referenced import Bill of Entry's own invoice serial number. Also printed as: Be Inv Sno")
    be_item_sno: Optional[str] = Field(default=None, alias='BE_Item_SNo', description="The referenced import Bill of Entry's own item serial number. Also printed as: Be Item Sno")
    be_import_port_code: Optional[str] = Field(default=None, alias='BE_Import_Port_Code', description='Port code where the original import happened. Also printed as: Be Import Port Code')
    manual_be: Optional[str] = Field(default=None, alias='Manual_BE', description='Y/N flag -- whether that Bill of Entry was manually filed (not electronic). Also printed as: Manual Be')
    be_item_description: Optional[str] = Field(default=None, alias='BE_Item_Description', description='Description of the item as it appeared on the import Bill of Entry. Also printed as: Be Item Description')
    imported_quantity: Optional[str] = Field(default=None, alias='Imported_Quantity', description='Quantity originally imported. Also printed as: Imported Quantity')
    imported_quantity_unit_code: Optional[str] = Field(default=None, alias='Imported_Quantity_Unit_Code', description='Unit code for that imported quantity. Also printed as: Imported Quantity Unit Code')
    assessable_value: Optional[float | str] = Field(default=None, alias='Assessable_Value', description='Assessable value at the time of import. Also printed as: Assessable Value')


class ExportExcReExportChunk1Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    tot_duty_paid: Optional[float | str] = Field(default=None, alias='Tot_Duty_Paid', description='Total duty paid at the time of import. Also printed as: Tot Duty Paid')
    duty_payment_date: Optional[str] = Field(default=None, alias='Duty_Payment_Date', description='Date that duty was paid. Also printed as: Duty Payment Date')
    exported_quantity: Optional[str] = Field(default=None, alias='Exported_Quantity', description='Quantity now being re-exported. Also printed as: Exported Quantity')
    exported_quantity_unit_code: Optional[str] = Field(default=None, alias='Exported_Quantity_Unit_Code', description='Unit code for that re-exported quantity. Also printed as: Exported Quantity Unit Code')
    technical_details: Optional[str] = Field(default=None, alias='Technical_Details', description='Technical specification details of the item. Also printed as: Technical Details')
    is_input_credit_availed: Optional[str] = Field(default=None, alias='Is_Input_Credit_Availed', description='Y/N flag -- whether input tax credit was availed on this item. Also printed as: Is Input Credit Availed')
    is_personal_use_item: Optional[str] = Field(default=None, alias='Is_Personal_Use_Item', description='Y/N flag -- whether the item was put to personal/own use before re-export. Also printed as: Is Personal Use Item')
    other_identifying_parameters: Optional[str] = Field(default=None, alias='Other_Identifying_Parameters', description='Any other identifying details distinguishing this specific unit/item. Also printed as: Other Identifying Parameters')
    obligation_no: Optional[str] = Field(default=None, alias='Obligation_No.', description='Reference number of any export obligation tied to this re-export. Also printed as: Obligation No.')
    dbk_amount_claimed: Optional[float | str] = Field(default=None, alias='DBK_Amount_Claimed', description='Drawback amount being claimed on re-export. Also printed as: Dbk Amount Claimed')
    is_item_un_used: Optional[str] = Field(default=None, alias='Is_Item_Un-Used', description='Y/N flag -- whether the item remains unused since import. Also printed as: Is Item Un-used')
    is_commissioner_permission: Optional[str] = Field(default=None, alias='Is_Commissioner_Permission', description="Y/N flag -- whether Commissioner's permission was obtained for this re-export. Also printed as: Is Commissioner Permission")


class ExportExcReExportChunk2Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    board_no: Optional[str] = Field(default=None, alias='Board_No.', description='Reference number of a CBIC (Board) circular/permission relied upon. Also printed as: Board No.')
    board_date: Optional[str] = Field(default=None, alias='Board_Date', description='Date of that Board circular/permission. Also printed as: Board Date')
    is_modvat_availed: Optional[str] = Field(default=None, alias='Is_MODVAT_Availed', description='Y/N flag -- whether MODVAT (legacy excise credit) was availed. Also printed as: Is Modvat Availed')
    is_modvat_reversed: Optional[str] = Field(default=None, alias='Is_MODVAT_Reversed', description='Y/N flag -- whether that MODVAT credit was reversed for this re-export. Also printed as: Is Modvat Reversed')


class ExportExcDucChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    invsrno: Optional[str] = Field(default=None, alias='InvSrNo', description='Serial number of this invoice within the shipping bill (a shipping bill can cover multiple invoices). Also printed as: Invsrno')
    itemsrno: Optional[str] = Field(default=None, alias='ItemSrNo', description='Serial number of this item within its invoice. Also printed as: Itemsrno')
    duc_reference_no: Optional[str] = Field(default=None, alias='DUC Reference No.', description='Duty Credit Scrip / DUC reference number. Also printed as: Duc Reference No.')
    export_type: Optional[str] = Field(default=None, alias='Export Type', description='Category of export this DUC reference applies to. Also printed as: Export Type')
    sb_date: Optional[str] = Field(default=None, alias='SB Date', description="The Shipping Bill's own date. Also printed as: Sb Date")
    be_no: Optional[str] = Field(default=None, alias='BE No.', description='Bill of Entry number referenced by this DUC entry. Also printed as: Be No.')
    be_date: Optional[str] = Field(default=None, alias='BE Date', description='Date of that referenced Bill of Entry. Also printed as: Be Date')
    be_filed_at: Optional[str] = Field(default=None, alias='BE Filed At', description='Port/location where that Bill of Entry was filed. Also printed as: Be Filed At')
    be_inv_sr: Optional[str] = Field(default=None, alias='BE Inv Sr', description="That Bill of Entry's own invoice serial number. Also printed as: Be Inv Sr")
    be_item_sr: Optional[str] = Field(default=None, alias='BE Item Sr', description="That Bill of Entry's own item serial number. Also printed as: Be Item Sr")


class ExportExcSwAddlInfoChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    inv_srno: Optional[str] = Field(default=None, alias='Inv_SrNo', description="Serial number of the invoice this row's data belongs to (links back to the INVOICES sheet). Also printed as: Inv Srno")
    item_srno: Optional[str] = Field(default=None, alias='Item_SrNo', description="Serial number of the item this row's data belongs to (links back to the ITEMS sheet). Also printed as: Item Srno")
    info_type: Optional[str] = Field(default=None, alias='Info_Type', description='The Single Window information type code, as defined by the regulating PGA (Participating Government Agency). Also printed as: Info Type')
    info_qualifier: Optional[str] = Field(default=None, alias='info_Qualifier', description='A qualifier further specifying that information type. Also printed as: Info Qualifier')
    info_code_description: Optional[str] = Field(default=None, alias='Info_Code_Description', description='Description of the information code. Also printed as: Info Code Description')
    information: Optional[str] = Field(default=None, alias='Information', description='The actual information/value being declared. Also printed as: Information')
    measure: Optional[str] = Field(default=None, alias='Measure', description='A numeric measure associated with this declaration. Also printed as: Measure')
    measure_unit: Optional[str] = Field(default=None, alias='Measure_Unit', description='Unit of that measure. Also printed as: Measure Unit')


class ExportExcSwConstituentChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    inv_srno: Optional[str] = Field(default=None, alias='Inv_SrNo', description="Serial number of the invoice this row's data belongs to (links back to the INVOICES sheet). Also printed as: Inv Srno")
    item_srno: Optional[str] = Field(default=None, alias='Item_SrNo', description="Serial number of the item this row's data belongs to (links back to the ITEMS sheet). Also printed as: Item Srno")
    cons_name: Optional[str] = Field(default=None, alias='Cons_Name', description='Name of the constituent/ingredient. Also printed as: Cons Name')
    cons_code: Optional[str] = Field(default=None, alias='Cons_Code', description='Code identifying that constituent. Also printed as: Cons Code')
    cons_percentage: Optional[float | str] = Field(default=None, alias='Cons_Percentage', description='Percentage of this constituent in the product. Also printed as: Cons Percentage')
    cons_yield: Optional[str] = Field(default=None, alias='Cons_Yield%', description='Yield percentage of this constituent. Also printed as: Cons Yield%')
    cons_active: Optional[str] = Field(default=None, alias='Cons_Active', description='Y/N flag -- whether this is an active ingredient/constituent. Also printed as: Cons Active')


class ExportExcSwProductionChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    inv_srno: Optional[str] = Field(default=None, alias='Inv_SrNo', description="Serial number of the invoice this row's data belongs to (links back to the INVOICES sheet). Also printed as: Inv Srno")
    item_srno: Optional[str] = Field(default=None, alias='Item_SrNo', description="Serial number of the item this row's data belongs to (links back to the ITEMS sheet). Also printed as: Item Srno")
    prod_batch_id: Optional[str] = Field(default=None, alias='Prod_Batch_ID', description='Production batch identifier. Also printed as: Prod Batch Id')
    prod_batch_quantity: Optional[str] = Field(default=None, alias='Prod_Batch_Quantity', description='Quantity produced in that batch. Also printed as: Prod Batch Quantity')
    prod_batch_unit: Optional[str] = Field(default=None, alias='Prod_Batch_Unit', description='Unit of that batch quantity. Also printed as: Prod Batch Unit')
    prod_manufacturer_date: Optional[str] = Field(default=None, alias='Prod_Manufacturer_Date', description='Date of manufacture. Also printed as: Prod Manufacturer Date')
    prod_expiry_date: Optional[str] = Field(default=None, alias='Prod_Expiry_Date', description='Expiry date. Also printed as: Prod Expiry Date')
    prod_best_before_date: Optional[str] = Field(default=None, alias='Prod_Best_before_Date', description='Best-before date. Also printed as: Prod Best Before Date')


class ExportExcSwControlChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    inv_srno: Optional[str] = Field(default=None, alias='Inv_SrNo', description="Serial number of the invoice this row's data belongs to (links back to the INVOICES sheet). Also printed as: Inv Srno")
    item_srno: Optional[str] = Field(default=None, alias='Item_SrNo', description="Serial number of the item this row's data belongs to (links back to the ITEMS sheet). Also printed as: Item Srno")
    control_type: Optional[str] = Field(default=None, alias='Control_Type', description='Type of regulatory control/inspection applied. Also printed as: Control Type')
    control_location: Optional[str] = Field(default=None, alias='Control_Location', description='Location where that control was carried out. Also printed as: Control Location')
    control_start_date: Optional[str] = Field(default=None, alias='Control_Start_Date', description='Start date of that control. Also printed as: Control Start Date')
    control_end_date: Optional[str] = Field(default=None, alias='Control_End_Date', description='End date of that control. Also printed as: Control End Date')
    control_result: Optional[str] = Field(default=None, alias='Control_Result', description='Result/outcome of that control. Also printed as: Control Result')


class ExportExcStatementChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    inv_srno: Optional[str] = Field(default=None, alias='Inv_SrNo', description="Serial number of the invoice this row's data belongs to (links back to the INVOICES sheet). Also printed as: Inv Srno")
    item_srno: Optional[str] = Field(default=None, alias='Item_SrNo', description="Serial number of the item this row's data belongs to (links back to the ITEMS sheet). Also printed as: Item Srno")
    statementtype: Optional[str] = Field(default=None, alias='StatementType', description='Category of statement being declared. Also printed as: Statementtype')
    statementcode: Optional[str] = Field(default=None, alias='StatementCode', description='Code identifying that statement. Also printed as: Statementcode')
    statementremarks: Optional[str] = Field(default=None, alias='StatementRemarks', description='Free-text remarks for that statement. Also printed as: Statementremarks')


class ExportExcESanchitChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    inv_srno: Optional[str] = Field(default=None, alias='Inv_SrNo', description="Serial number of the invoice this row's data belongs to (links back to the INVOICES sheet). Also printed as: Inv Srno")
    item_srno: Optional[str] = Field(default=None, alias='Item_SrNo', description="Serial number of the item this row's data belongs to (links back to the ITEMS sheet). Also printed as: Item Srno")
    doc_icegate_id: Optional[str] = Field(default=None, alias='Doc_ICEGATE_ID', description='The ICEGATE-assigned ID for this uploaded document. Also printed as: Doc Icegate Id')
    doc_irn: Optional[str] = Field(default=None, alias='Doc_IRN', description="The document's Image Reference Number (e-Sanchit's unique document identifier). Also printed as: Doc Irn")
    doc_upload_datetime: Optional[str] = Field(default=None, alias='Doc_Upload_DateTime', description='Date/time the document was uploaded. Also printed as: Doc Upload Datetime')
    doc_type: Optional[str] = Field(default=None, alias='Doc_Type', description='Type/category of the uploaded document (e.g. Certificate of Origin, Packing List, License copy). Also printed as: Doc Type')
    file_type: Optional[str] = Field(default=None, alias='File_Type', description='File format of the uploaded document. Also printed as: File Type')
    icegate_file_name: Optional[str] = Field(default=None, alias='Icegate_File_Name', description='The filename as registered on ICEGATE. Also printed as: Icegate File Name')
    document_name: Optional[str] = Field(default=None, alias='Document_Name', description='Descriptive name of the document. Also printed as: Document Name')
    reference_no: Optional[str] = Field(default=None, alias='Reference_No.', description="The document's own reference number. Also printed as: Reference No.")
    doc_issued_at: Optional[str] = Field(default=None, alias='Doc_Issued_At', description='Place the document was issued. Also printed as: Doc Issued At')
    doc_issued_date: Optional[str] = Field(default=None, alias='Doc_Issued_Date', description='Date the document was issued. Also printed as: Doc Issued Date')


class ExportExcESanchitChunk1Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    doc_expiry_date: Optional[str] = Field(default=None, alias='Doc_Expiry_Date', description='Expiry date of the document, if applicable. Also printed as: Doc Expiry Date')
    doc_issuing_party_name: Optional[str] = Field(default=None, alias='Doc_Issuing_Party_Name', description='Name of the party that issued this document. Also printed as: Doc Issuing Party Name')
    doc_issuing_party_code: Optional[str] = Field(default=None, alias='Doc_Issuing_Party_Code', description='Code of the party that issued this document. Also printed as: Doc Issuing Party Code')
    doc_issuing_party_add1: Optional[str] = Field(default=None, alias='Doc_Issuing_Party_Add1', description="Issuing party's address line 1. Also printed as: Doc Issuing Party Add1")
    doc_issuing_party_add2: Optional[str] = Field(default=None, alias='Doc_Issuing_Party_Add2', description="Issuing party's address line 2. Also printed as: Doc Issuing Party Add2")
    doc_issuing_party_city: Optional[str] = Field(default=None, alias='Doc_Issuing_Party_City', description="Issuing party's city. Also printed as: Doc Issuing Party City")
    doc_issuing_party_pin_code: Optional[str] = Field(default=None, alias='Doc_Issuing_Party_Pin_Code', description="Issuing party's postal/PIN code. Also printed as: Doc Issuing Party Pin Code")
    doc_beneficiary_party_name: Optional[str] = Field(default=None, alias='Doc_Beneficiary_Party_Name', description='Name of the party this document benefits/is addressed to. Also printed as: Doc Beneficiary Party Name')
    doc_beneficiary_party_code: Optional[str] = Field(default=None, alias='Doc_Beneficiary_Party_Code', description='Code of that beneficiary party. Also printed as: Doc Beneficiary Party Code')
    doc_beneficiary_party_add1: Optional[str] = Field(default=None, alias='Doc_Beneficiary_Party_Add1', description="Beneficiary party's address line 1. Also printed as: Doc Beneficiary Party Add1")
    doc_beneficiary_party_add2: Optional[str] = Field(default=None, alias='Doc_Beneficiary_Party_Add2', description="Beneficiary party's address line 2. Also printed as: Doc Beneficiary Party Add2")
    doc_beneficiary_party_city: Optional[str] = Field(default=None, alias='Doc_Beneficiary_Party_City', description="Beneficiary party's city. Also printed as: Doc Beneficiary Party City")


class ExportExcESanchitChunk2Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    doc_beneficiary_party_pin_code: Optional[str] = Field(default=None, alias='Doc_Beneficiary_Party_Pin_Code', description="Beneficiary party's postal/PIN code. Also printed as: Doc Beneficiary Party Pin Code")


class ExportExcExchangeRateChunk0Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    currency_code: Optional[str] = Field(default=None, alias='CURRENCY_CODE', description='The foreign currency this exchange rate applies to. Also printed as: Currency Code')
    exchange_rate: Optional[float | str] = Field(default=None, alias='EXCHANGE_RATE', description='The exchange rate value (foreign currency to INR). Also printed as: Exchange Rate')
    bank_name: Optional[str] = Field(default=None, alias='BANK_NAME', description='Name of the bank whose rate/certificate is being used. Also printed as: Bank Name')
    bank_certificate: Optional[str] = Field(default=None, alias='BANK_CERTIFICATE', description="Reference to the bank's exchange rate certificate. Also printed as: Bank Certificate")
    bank_certificate_date: Optional[str] = Field(default=None, alias='BANK_CERTIFICATE_DATE', description='Date of that bank certificate. Also printed as: Bank Certificate Date')


CHUNK_MODELS = {
    ('GENERAL', 0): ExportExcGeneralChunk0Row,
    ('GENERAL', 1): ExportExcGeneralChunk1Row,
    ('GENERAL', 2): ExportExcGeneralChunk2Row,
    ('GENERAL', 3): ExportExcGeneralChunk3Row,
    ('SEZ_INFO', 0): ExportExcSezInfoChunk0Row,
    ('SEZ_INFO', 1): ExportExcSezInfoChunk1Row,
    ('SEZ_INFO', 2): ExportExcSezInfoChunk2Row,
    ('PACKING_DETAIL', 0): ExportExcPackingDetailChunk0Row,
    ('STUFFING_INFO', 0): ExportExcStuffingInfoChunk0Row,
    ('ANNEXURE_C1', 0): ExportExcAnnexureC1Chunk0Row,
    ('ANNEXURE_C1', 1): ExportExcAnnexureC1Chunk1Row,
    ('EXBOND_INFO', 0): ExportExcExbondInfoChunk0Row,
    ('CONTAINER', 0): ExportExcContainerChunk0Row,
    ('INVOICES', 0): ExportExcInvoicesChunk0Row,
    ('INVOICES', 1): ExportExcInvoicesChunk1Row,
    ('INVOICES', 2): ExportExcInvoicesChunk2Row,
    ('INVOICES', 3): ExportExcInvoicesChunk3Row,
    ('ITEMS', 0): ExportExcItemsChunk0Row,
    ('ITEMS', 1): ExportExcItemsChunk1Row,
    ('ITEMS', 2): ExportExcItemsChunk2Row,
    ('ITEMS', 3): ExportExcItemsChunk3Row,
    ('ITEMS', 4): ExportExcItemsChunk4Row,
    ('DEEC', 0): ExportExcDeecChunk0Row,
    ('EPCG', 0): ExportExcEpcgChunk0Row,
    ('DFIA', 0): ExportExcDfiaChunk0Row,
    ('DFRC', 0): ExportExcDfrcChunk0Row,
    ('CESS_AND_DUTY', 0): ExportExcCessAndDutyChunk0Row,
    ('QUOTA', 0): ExportExcQuotaChunk0Row,
    ('ARE_DETAILS', 0): ExportExcAreDetailsChunk0Row,
    ('JOB_WORK', 0): ExportExcJobWorkChunk0Row,
    ('RE-EXPORT', 0): ExportExcReExportChunk0Row,
    ('RE-EXPORT', 1): ExportExcReExportChunk1Row,
    ('RE-EXPORT', 2): ExportExcReExportChunk2Row,
    ('DUC', 0): ExportExcDucChunk0Row,
    ('SW_ADDL_INFO', 0): ExportExcSwAddlInfoChunk0Row,
    ('SW_CONSTITUENT', 0): ExportExcSwConstituentChunk0Row,
    ('SW_PRODUCTION', 0): ExportExcSwProductionChunk0Row,
    ('SW_CONTROL', 0): ExportExcSwControlChunk0Row,
    ('STATEMENT', 0): ExportExcStatementChunk0Row,
    ('E-SANCHIT', 0): ExportExcESanchitChunk0Row,
    ('E-SANCHIT', 1): ExportExcESanchitChunk1Row,
    ('E-SANCHIT', 2): ExportExcESanchitChunk2Row,
    ('EXCHANGE_RATE', 0): ExportExcExchangeRateChunk0Row,
}


# Copyright (c) 2023, Microsynth, libracore and contributors and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
import json

def execute(filters=None):
    columns = get_columns(filters)
    data = get_data(filters)
    return columns, data

def get_columns(filters):
    if filters.get("version") == "AT":
        columns = [
            {"label": _("gkonto"), "fieldname": "account", "fieldtype": "Data", "width": 80},
            {"label": _("belegnr"), "fieldname": "document", "fieldtype": "Dynamic Link", "options": "document_type", "width": 120},
            {"label": _("belegdatum"), "fieldname": "date", "fieldtype": "Date", "width": 80},
            {"label": _("buchsymbol"), "fieldname": "book_symbol", "fieldtype": "Data", "width": 80},
            {"label": _("buchcode"), "fieldname": "book_code", "fieldtype": "Data", "width": 80},
            {"label": _("prozent"), "fieldname": "vat_percent", "fieldtype": "Percent", "width": 80},
            {"label": _("steuercode"), "fieldname": "vat_code", "fieldtype": "Data", "width": 80},
            {"label": _("betrag"), "fieldname": "gross_amount", "fieldtype": "Float", "width": 100, "precision": 2},
            {"label": _("steuer"), "fieldname": "vat_amount", "fieldtype": "float", "width": 100, "precision": 2},
            {"label": _("text"), "fieldname": "description", "fieldtype": "Data", "width": 120},
        ]
    return columns

def get_data(filters, short=False):
    sql_query = ""
    if filters.get("version") == "AT":
        sql_query = """
        SELECT
            (SELECT
                SUBSTRING(`tabSales Invoice Item`.`income_account`, 1, 4)
             FROM `tabSales Invoice Item`
             WHERE `tabSales Invoice Item`.`parent` = `tabSales Invoice`.`name`
             ORDER BY `tabSales Invoice Item`.`idx` ASC
             LIMIT 1) AS `account`,
            "Sales Invoice" AS `document_type`,
            `tabSales Invoice`.`name` as `document`,
            `tabSales Invoice`.`posting_date` as `date`,
            "AR" AS `book_symbol`,
            "" AS `book_code`,
            ROUND(100 * `tabSales Invoice`.`total_taxes_and_charges` / `tabSales Invoice`.`net_total`, 1) AS `vat_percent`,
            `tabSales Invoice`.`grand_total` AS `gross_amount`,
            (-1) * `tabSales Invoice`.`total_taxes_and_charges`  AS `vat_amount`,
            "Rechnung" AS `description`
        FROM `tabSales Invoice`
        WHERE 
            `tabSales Invoice`.`docstatus` = 1
            AND `tabSales Invoice`.`company` = "{company}"
            AND `tabSales Invoice`.`posting_date` >= "{from_date}"
            AND `tabSales Invoice`.`posting_date` <= "{to_date}"
        """.format(
            company=filters.get("company"), 
            from_date=filters.get("from_date"), 
            to_date=filters.get("to_date"))

    data = frappe.db.sql(sql_query, as_dict=True)
    
    return data

@frappe.whitelist()
def async_pdf_export(filters):
    if type(filters) == str:
        filters = json.loads(filters)
        
    frappe.enqueue(method=pdf_export, queue='long', timeout=90, is_async=True, filters=filters)
    return
    
def pdf_export(filters):
    data = get_data(filters)
    settings = frappe.get_doc("Microsynth Settings", "Microsynth Settings")
    
    for d in data:
        if d.get("document_type") == "Sales Invoice":
            content_pdf = frappe.get_print(
                d.get("document_type"), 
                d.get("document"), 
                print_format=settings.pdf_print_format, 
                as_pdf=True)
            content_file_name = "{0}/{1}.pdf".format(settings.pdf_export_path, d.get("document"))
            with open(content_file_name, mode='wb') as file:
                file.write(content_pdf)

    return

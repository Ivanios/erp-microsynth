# -*- coding: utf-8 -*-
# Copyright (c) 2023, Microsynth, libracore and contributors and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import cint, get_url_to_form
from datetime import date

class CustomsDeclaration(Document):
    pass

@frappe.whitelist()
def create_customs_declaration():
    cd = frappe.get_doc({
        'doctype':'Customs Declaration',
        'company': frappe.defaults.get_global_default('company'),
        'date': date.today()
        })
    dns = get_delivery_notes_to_declare();
    for dn in dns:
        if dn['export_category'] == "AT":
            cd.append('austria_dns',dn)
        if dn['export_category'] == "EU":
            cd.append('eu_dns',dn)
    cd.insert()
    frappe.db.commit()
    return get_url_to_form("Customs Declaration", cd.name)

def get_delivery_notes_to_declare():
    sql_query = """SELECT
        `tabDelivery Note`.`name` as `delivery_note`,
        `tabDelivery Note`.`customer`,
        `tabDelivery Note`.`customer_name`,
        `tabDelivery Note`.`export_category`,
        `tabDelivery Note`.`shipping_address_name` as `shipping_address`,
        `tabCustomer`.`tax_id` as `tax_id`,
        `tabAddress`.`country` as `country`,
        `tabDelivery Note`.`currency`,
        `tabDelivery Note`.`total`,
        `tabDelivery Note`.`grand_total`
        FROM `tabDelivery Note`
        JOIN `tabCustomer` ON  `tabCustomer`.`name` = `tabDelivery Note`.`customer`
        JOIN `tabAddress` ON `tabAddress`.`name` = `tabDelivery Note`.`shipping_address_name`
        WHERE `tabDelivery Note`.`export_category` IN ('AT', 'EU')
        AND `tabDelivery Note`.`customs_declaration` is NULL;
        """
    delivery_notes = frappe.db.sql(sql_query, as_dict=True)
    return delivery_notes
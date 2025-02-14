# Copyright (c) 2022, Microsynth, libracore and contributors and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import os
import frappe
from frappe import _
import json
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from microsynth.microsynth.naming_series import get_naming_series

def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data

def get_columns():
    return [
        {"label": _("Sales Order"), "fieldname": "sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 120},
        {"label": _("Web ID"), "fieldname": "web_order_id", "fieldtype": "Data", "width": 70},
        {"label": _("Punchout"), "fieldname": "is_punchout", "fieldtype": "Check", "width": 75},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 70},
        {"label": _("Customer name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 150},
        {"label": _("Contact"), "fieldname": "contact", "fieldtype": "Link", "options": "Contact", "width": 60},
        {"label": _("Contact name"), "fieldname": "contact_name", "fieldtype": "Data", "width": 150},
        {"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 80},
        {"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 200},
        {"label": _("Qty"), "fieldname": "qty", "fieldtype": "Integer", "width": 50},
        # {"label": _("Range"), "fieldname": "range", "fieldtype": "Data", "width": 80},
        {"label": _("Comment"), "fieldname": "comment", "fieldtype": "Data", "width": 100}
    ]

@frappe.whitelist()
def get_data(filters=None):
    if type(filters) == str:
        filters = json.loads(filters)
    elif type(filters) == dict:
        pass
    else:
        filters = dict(filters)

    open_label_orders = frappe.db.sql("""
        SELECT 
            `tabSales Order`.`name` AS `sales_order`,
            `tabSales Order`.`web_order_id` AS `web_order_id`,
            `tabSales Order`.`is_punchout` AS `is_punchout`,
            `tabSales Order`.`customer` AS `customer`,
            `tabSales Order`.`customer_name` AS `customer_name`,
            `tabSales Order`.`contact_person` AS `contact`,
            `tabSales Order`.`contact_display` AS `contact_name`,
            `tabSales Order`.`transaction_date` AS `date`,
            `tabSales Order Item`.`item_code` AS `item_code`,
            `tabSales Order Item`.`item_name` AS `item_name`,
            `tabSales Order Item`.`qty` AS `qty`,
            `tabLabel Range`.`range` AS `range`,
            `tabSales Order`.`comment` AS `comment`
        FROM `tabSales Order`
        LEFT JOIN `tabSales Order Item` ON
            (`tabSales Order Item`.`parent` = `tabSales Order`.`name` AND `tabSales Order Item`.`idx` = 1)
        LEFT JOIN `tabLabel Range` ON
            (`tabLabel Range`.`item_code` = `tabSales Order Item`.`item_code`)
        WHERE 
            `tabSales Order`.`product_type` = "Labels"
            AND `tabSales Order`.`docstatus` = 1
            AND `tabSales Order`.`company` = "{company}"
            AND `tabSales Order`.`transaction_date` > '2022-12-22'
            AND `tabSales Order`.`hold_order` <> 1
            AND NOT EXISTS (SELECT *
                            FROM `tabDelivery Note Item`
                            WHERE `tabDelivery Note Item`.`against_sales_order` = `tabSales Order`.`name`
                            AND `tabDelivery Note Item`.`docstatus` <> 2)
        ORDER BY `tabSales Order Item`.`item_code`, `tabSales Order`.`transaction_date` ASC;
    """.format(company=filters.get("company")), as_dict=True)
    
    return open_label_orders

ASSIGNMENT_HEADER = """Person_ID\tSales_Order\tWeb_Order_ID\tItem\tStart\tEnd\n"""
ASSIGNMENT_FIELDS = """{person_id}\t{sales_order}\t{web_order_id}\t{item}\t{start}\t{end}\n"""

def write_assignment_file(data):
    path = frappe.get_value("Sequencing Settings", "Sequencing Settings", "label_export_path")
    assignment_file = "{path}/{sales_order}.tab".format(path = path, sales_order=data["sales_order"])
    if os.path.exists(assignment_file):
        frappe.throw("<b>Sequencing label assignment file already exists:</b><br>" + assignment_file)
    else:
        file = open(assignment_file, "w")
        file.write(ASSIGNMENT_HEADER)
        line = ASSIGNMENT_FIELDS.format(
            person_id = data["person_id"],
            sales_order = data["sales_order"],
            web_order_id = data["web_order_id"],
            item = data["item"],
            start = data["from_barcode"],
            end = data["to_barcode"]
        )
        file.write(line)
        file.close()
    return


@frappe.whitelist()
def pick_labels(sales_order, from_barcode, to_barcode):
    # create sequencing labels
    item = frappe.db.sql("""SELECT `item_code`
        FROM `tabSales Order Item`
        WHERE `parent` = "{sales_order}"
        ORDER BY `idx` ASC
        LIMIT 1;""".format(sales_order=sales_order), as_dict=True)[0]['item_code']
    company = frappe.get_value("Sales Order", sales_order, "company")
    customer = frappe.get_value("Sales Order", sales_order, "customer")
    customer_name = frappe.get_value("Sales Order", sales_order, "customer_name")
    contact = frappe.get_value("Sales Order", sales_order, "contact_person")
    web_order_id = frappe.get_value("Sales Order", sales_order, "web_order_id")
    register_labels = frappe.get_value("Sales Order", sales_order, "register_labels")
    for i in range(int(from_barcode), (int(to_barcode) + 1)):
        # create label
        new_label = frappe.get_doc({
            'doctype': 'Sequencing Label',
            'item': item,
            'label_id': str(i),
            'sales_order': sales_order,
            'customer': customer,
            'customer_name': customer_name,
            'status': "unused",
            'contact': contact,
            'registered_to': contact if register_labels else None
        }).insert()
    frappe.db.commit()
    
    # create delivery note
    dn_content = make_delivery_note(sales_order)
    dn = frappe.get_doc(dn_content)
    dn.naming_series = get_naming_series("Delivery Note", company)
    dn.insert()
    dn.submit()
    frappe.db.commit()
    
    assignment_data = {
            "person_id": contact,
            "sales_order": sales_order,
            "web_order_id": web_order_id,
            "item": item,
            "from_barcode": from_barcode,
            "to_barcode": to_barcode
    }
    write_assignment_file(assignment_data)
    # print address label
    # TODO: see labels.py, take data from delivery note and pass to printer
    
    # return print format
    return dn.name
